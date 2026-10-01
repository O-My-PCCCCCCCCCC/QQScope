# -*- coding: utf-8 -*-
"""决定性实验：NT blob 里的命名名片，在 qqscope.db 里是不是 bare？"""
import sys, json
from pathlib import Path
from collections import Counter
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
import sqlcipher3.dbapi2 as sc
from core import store
from msgdb.proto.c2c_40800_parser import parse_40800
from msgdb.group import exporter as group_exporter
from msgdb.c2c import parser as c2c_parser
from core.sources._pack_extract import build_media

QQ = 1605289411
DB = ROOT / "data" / "decrypt" / str(QQ) / "nt_msg_clear.db"
KEY = (ROOT / "data" / "keys" / (str(QQ) + ".key")).read_text(encoding="utf-8").strip()
con = sc.connect(str(DB), isolation_level=None)
for p in ("PRAGMA cipher_page_size = 4096;", "PRAGMA key = '%s';" % KEY,
          "PRAGMA kdf_iter = 4000;", "PRAGMA cipher_hmac_algorithm = HMAC_SHA1;",
          "PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;"):
    con.execute(p)

sq = store.connect()
def db_lookup(kind, peer_id, ts, direction, text):
    r = sq.execute("SELECT media FROM messages WHERE account_qq=? AND kind=? AND peer_id=? AND ts=? AND direction=? AND COALESCE(text,'')=COALESCE(?,'')",
                   (QQ, kind, peer_id, ts, direction, text)).fetchone()
    return json.loads(r["media"] or "{}") if r else None

stat = Counter()
samples = []
def handle(rec, payload):
    media = payload
    fb = str(media.get("fallback") or "")
    if media.get("kind") != "card": return
    kind = rec["kind"]; peer_id = rec["peer_id"]; ts = rec["ts"]; direction = rec["direction"]; text = rec["text"]
    row = db_lookup(kind, peer_id, ts, direction, text)
    if row is None:
        stat["db_missing"] += 1; return
    dbfb = str(row.get("fallback") or "")
    if fb == "[名片]":
        stat["nt_bare"] += 1
        stat["nt_bare_dbfb=" + ("[名片]" if dbfb == "[名片]" else "其它")] += 1
    elif fb.startswith("[名片] "):
        stat["nt_named"] += 1
        stat["nt_named_dbfb=" + ("[名片]" if dbfb == "[名片]" else ("同名" if dbfb == fb else "其它"))] += 1
    else:
        stat["nt_other_label"] += 1
    if len(samples) < 8 and fb.startswith("[名片] ") and dbfb == "[名片]":
        samples.append((kind, peer_id, ts, fb))

cur = con.execute(c2c_parser.SELECT_SQL)
for raw in cur:
    cols = [d[0] for d in cur.description]; r = dict(zip(cols, raw))
    if not r.get("blob"): continue
    mo = build_media(list(parse_40800(r["blob"]).contents))
    if not mo: continue
    try: sqq = int(r.get("sender_qq") or 0)
    except Exception: sqq = 0
    try: pq = int(r.get("peer_qq") or 0)
    except Exception: pq = 0
    rec = {"kind": "c2c", "peer_id": str(pq) if pq else (str(r.get("peer_uid") or "").strip() or "0"),
           "ts": int(r.get("timestamp") or 0), "direction": 1 if (sqq and sqq == QQ) else 0,
           "text": None}
    # text: from parser
    try:
        m = c2c_parser.parse_row(r); rec["text"] = m.text
    except Exception: pass
    handle(rec, mo)

cur = con.execute(group_exporter.SELECT_SQL)
for raw in cur:
    cols = [d[0] for d in cur.description]; r = dict(zip(cols, raw))
    if not r.get("blob"): continue
    mo = build_media(list(parse_40800(r["blob"]).contents))
    if not mo: continue
    try: rec = group_exporter.parse_row(r)
    except Exception: continue
    try: sqq = int(rec.get("sender_qq") or 0)
    except Exception: sqq = 0
    gi = str(rec.get("group_id") or "").strip()
    try: gq = int(rec.get("group_qq") or 0)
    except Exception: gq = 0
    if gq <= 0 and gi.isdigit(): gq = int(gi)
    h = {"kind": "group", "peer_id": gi or str(gq), "ts": int(rec.get("timestamp") or 0),
         "direction": 1 if (sqq and sqq == QQ) else 0, "text": rec.get("text")}
    handle(h, mo)

for k, v in sorted(stat.items()):
    print("%-30s %d" % (k, v))
print("samples:", samples)
con.close(); sq.close()

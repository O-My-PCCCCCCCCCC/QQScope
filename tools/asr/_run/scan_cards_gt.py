# -*- coding: utf-8 -*-
"""NT 库 ground truth：c2c + group 的 card 统计（只读，不写库）。"""
import sys, json, time
from pathlib import Path
from collections import Counter
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
import sqlcipher3.dbapi2 as sc
from msgdb.proto.c2c_40800_parser import parse_40800
from msgdb.group import exporter as group_exporter
from msgdb.c2c import parser as c2c_parser
from core.sources._pack_extract import build_media, _classify

DB = ROOT / "data" / "decrypt" / "1605289411" / "nt_msg_clear.db"
KEY = (ROOT / "data" / "keys" / "1605289411.key").read_text(encoding="utf-8").strip()
con = sc.connect(str(DB), isolation_level=None)
for p in ("PRAGMA cipher_page_size = 4096;", f"PRAGMA key = '{KEY}';",
          "PRAGMA kdf_iter = 4000;", "PRAGMA cipher_hmac_algorithm = HMAC_SHA1;",
          "PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;"):
    con.execute(p)

def scan(sql, tag):
    cur = con.execute(sql)
    n=0; card=0; bare=0; named=0; uid_only=0
    kinds=Counter(); ct_card=Counter()
    bare_ct=Counter(); bare_uid=0; bare_nick=0
    named_sample=[]
    t0=time.time()
    for raw in cur:
        cols = [d[0] for d in cur.description]
        r = dict(zip(cols, raw))
        n+=1
        blob = r.get("blob")
        if not blob: continue
        try:
            res = parse_40800(blob)
            contents = list(res.contents)
        except Exception:
            continue
        if not contents: continue
        try:
            mo = build_media(contents)
        except Exception:
            mo = None
        if not mo: continue
        kinds[mo.get("kind")] = kinds.get(mo.get("kind"),0)+1
        if mo.get("kind") != "card":
            continue
        card+=1
        fb = str(mo.get("fallback") or "")
        nm = str(mo.get("name") or "").strip()
        uid = str(mo.get("uid") or "").strip()
        cts = tuple(int(c.content_type or 0) for c in contents)
        ct_card[cts]+=1
        if fb == "[名片]":
            bare+=1
            bare_ct[cts]+=1
            if uid: bare_uid+=1
        else:
            named+=1
            if len(named_sample)<3: named_sample.append((r.get("msg_id"), fb, uid))
        if uid and not nm: uid_only+=1
    print(f"[{tag}] rows={n} media_kinds={dict(kinds)}")
    print(f"[{tag}] cards={card} bare=[名片]={bare} named={named} uid_only={uid_only} bare_with_uid={bare_uid}")
    print(f"[{tag}] card ct-patterns={dict(ct_card.most_common(8))}")
    print(f"[{tag}] bare ct-patterns={dict(bare_ct.most_common(8))}")
    print(f"[{tag}] named samples={named_sample}  scan_sec={round(time.time()-t0,1)}")

scan(c2c_parser.SELECT_SQL, "c2c")
scan(group_exporter.SELECT_SQL, "group")
con.close()

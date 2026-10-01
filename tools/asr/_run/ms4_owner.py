# -*- coding: utf-8 -*-
"""ms4（media_sub=4，新布局名片）字段归属：A=48503/04/05, B=48506/07/08，谁是名片主人？"""
import sys
from pathlib import Path
from collections import Counter
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
from core.sources._pack_extract import open_enc
from core.sources import names
from msgdb.proto.c2c_40800_parser import parse_40800
from msgdb.proto import wire
from msgdb.group import exporter as group_exporter
clear = ROOT / "data" / "decrypt" / "1605289411" / "nt_msg_clear.db"
KEY = (ROOT / "data" / "keys" / "1605289411.key").read_text(encoding="utf-8").strip()
con, _ = open_enc(clear, [KEY])
got = names.fetch_names(1605289411)
uid2qq = {u: v.get("qq") for u, v in (got.get("people") or {}).items() if isinstance(v, dict)}
qq2uid = {v: k for k, v in uid2qq.items() if v}
OWNER = uid2qq.get("u_8-Rg7t9qh5U3kbUAm76ipg")
print("owner uid->qq:", "u_8-Rg7t9qh5U3kbUAm76ipg", OWNER, "| owner_qq=1605289411")
def getf(fs, *nums):
    out = {}
    for f in fs:
        if f.wire_type == 2 and f.number in nums:
            try: out[f.number] = f.raw_value.decode("utf-8", "replace")
            except Exception: pass
    return out
st = Counter(); samples = []
cur = con.execute(group_exporter.SELECT_SQL); cols = [d[0] for d in cur.description]
for raw in cur:
    r = dict(zip(cols, raw)); blob = r.get("blob")
    if not blob: continue
    try: cs = list(parse_40800(blob).contents)
    except Exception: continue
    for c in cs:
        if int(c.content_type or 0) != 8 or int(c.media_sub or 0) != 4: continue
        fs = wire.parse_wire(c.SerializeToString())
        d = getf(fs, 48503, 48504, 48506, 48507)
        uA, nA, uB, nB = d.get(48503, ""), d.get(48504, ""), d.get(48506, ""), d.get(48507, "")
        sq = r.get("sender_qq")
        snd_uid = qq2uid.get(sq) if sq else (OWNER if int(r.get("direction") or 0) == 1 and int(sq or 0) == 1605289411 else None)
        st["n"] += 1
        if snd_uid:
            st["has_sender_uid"] += 1
            if uA == snd_uid: st["A==sender"] += 1
            if uB == snd_uid: st["B==sender"] += 1
        if uA == OWNER: st["A==owner"] += 1
        if uB == OWNER: st["B==owner"] += 1
        if len(samples) < 8:
            samples.append((r.get("msg_id"), "dir=%s sq=%s snd_uid=%s" % (r.get("direction"), sq, snd_uid), nA + "/" + uA, nB + "/" + uB))
for k, v in st.items(): print("%-18s %d" % (k, v))
for s in samples: print(s)
con.close()

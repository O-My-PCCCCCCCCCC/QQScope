# -*- coding: utf-8 -*-
import sys
from pathlib import Path
from collections import Counter
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
from core.sources._pack_extract import open_enc, build_media
from msgdb.proto.c2c_40800_parser import parse_40800
from msgdb.group import exporter as group_exporter
from msgdb.c2c import parser as c2c_parser
clear = ROOT / "data" / "decrypt" / "1605289411" / "nt_msg_clear.db"
KEY = (ROOT / "data" / "keys" / "1605289411.key").read_text(encoding="utf-8").strip()
con, _ = open_enc(clear, [KEY])
ctr = Counter(); samples = {}
def scan(sql):
    cur = con.execute(sql); cols=[d[0] for d in cur.description]
    for raw in cur:
        r = dict(zip(cols, raw)); blob = r.get("blob")
        if not blob: continue
        try: cs = list(parse_40800(blob).contents)
        except Exception: continue
        mo = build_media(cs)
        if not mo or mo.get("kind") != "card": continue
        fb = str(mo.get("fallback") or "")
        key = fb.split(" ")[0] if fb else "(empty)"
        ctr[key] += 1
        if key not in samples:
            samples[key] = (r.get("msg_id"), fb[:70])
scan(c2c_parser.SELECT_SQL); scan(group_exporter.SELECT_SQL)
for k, v in ctr.most_common(12):
    print("%-12s %6d   ex=%s" % (k, v, samples.get(k)))
print("total cards:", sum(ctr.values()))
con.close()

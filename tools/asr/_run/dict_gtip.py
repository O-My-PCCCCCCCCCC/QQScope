# -*- coding: utf-8 -*-
import sys, json
from pathlib import Path
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
from core.sources._pack_extract import open_enc
from msgdb.proto.c2c_40800_parser import parse_40800
from msgdb.group import exporter as group_exporter
from google.protobuf.json_format import MessageToDict
clear = ROOT / "data" / "decrypt" / "1605289411" / "nt_msg_clear.db"
KEY = (ROOT / "data" / "keys" / "1605289411.key").read_text(encoding="utf-8").strip()
con, _ = open_enc(clear, [KEY])
cur = con.execute(group_exporter.SELECT_SQL); cols=[d[0] for d in cur.description]
shown = 0
for raw in cur:
    r=dict(zip(cols,raw)); blob=r.get("blob")
    if not blob: continue
    try: cs=list(parse_40800(blob).contents)
    except Exception: continue
    for c in cs:
        if int(c.content_type or 0)!=8: continue
        if getattr(c,"reply_f48271",""): continue
        if c.nc_uid_1 or c.nc_nickname_1: continue
        d = MessageToDict(c, preserving_proto_field_name=True)
        print("=== msg_id", r.get("msg_id"), "keys:", sorted(d.keys()))
        print(json.dumps(d, ensure_ascii=False)[:1000])
        shown += 1
        break
    if shown >= 3: break
con.close()

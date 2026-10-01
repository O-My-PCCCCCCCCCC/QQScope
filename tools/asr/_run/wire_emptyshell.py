# -*- coding: utf-8 -*-
import sys
from pathlib import Path
from collections import Counter
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
from core.sources._pack_extract import open_enc
from msgdb.proto.c2c_40800_parser import parse_40800
from msgdb.group import exporter as group_exporter
from msgdb.proto import wire
clear = ROOT / "data" / "decrypt" / "1605289411" / "nt_msg_clear.db"
KEY = (ROOT / "data" / "keys" / "1605289411.key").read_text(encoding="utf-8").strip()
con, _ = open_enc(clear, [KEY])
def val_of(f):
    if f.wire_type == 0: return f.value
    if f.wire_type == 2:
        try:
            s = f.raw_value.decode("utf-8")
            if s.strip() and all(ch.isprintable() or ch in "\t\n" for ch in s): return "str:" + s
        except Exception: pass
        return f"bytes[{len(f.raw_value)}]"
    return f"raw:{f.raw_value.hex()[:16]}"
ms = Counter(); shown = 0
cur = con.execute(group_exporter.SELECT_SQL); cols=[d[0] for d in cur.description]
for raw in cur:
    r=dict(zip(cols,raw)); blob=r.get("blob")
    if not blob: continue
    try: cs=list(parse_40800(blob).contents)
    except Exception: continue
    for c in cs:
        if int(c.content_type or 0)!=8: continue
        if c.nc_uid_1 or c.nc_nickname_1 or c.reply_f48271 or c.filename or c.ref_f47713: continue
        rawb = c.SerializeToString()
        try: fs = wire.parse_wire(rawb)
        except Exception: continue
        has_gtip = any(f.number==48214 and f.wire_type==2 and b"gtip" in f.raw_value for f in fs)
        if has_gtip: continue
        ms[int(c.media_sub or 0)] += 1
        if shown < 3:
            shown += 1
            print("=== msg_id", r.get("msg_id"), "media_sub", c.media_sub)
            for f in fs:
                print("   f%-7d %s" % (f.number, val_of(f)))
print("empty-shell media_sub counter:", dict(ms))
con.close()

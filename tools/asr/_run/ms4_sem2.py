# -*- coding: utf-8 -*-
import sys
from pathlib import Path
from collections import Counter
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
from core.sources._pack_extract import open_enc
from msgdb.proto.c2c_40800_parser import parse_40800
from msgdb.proto import wire
from msgdb.c2c import parser as c2c_parser
from msgdb.group import exporter as group_exporter
clear = ROOT / "data" / "decrypt" / "1605289411" / "nt_msg_clear.db"
KEY = (ROOT / "data" / "keys" / "1605289411.key").read_text(encoding="utf-8").strip()
con, _ = open_enc(clear, [KEY])
def getf(fs, num):
    for f in fs:
        if f.number == num and f.wire_type == 2:
            try: return f.raw_value.decode("utf-8","replace")
            except Exception: return ""
    return ""
def scan(sql, tag):
    cur = con.execute(sql); cols=[d[0] for d in cur.description]
    st = Counter(); rows=[]
    for raw in cur:
        r = dict(zip(cols, raw)); blob = r.get("blob")
        if not blob: continue
        try: cs=list(parse_40800(blob).contents)
        except Exception: continue
        for c in cs:
            if int(c.content_type or 0)!=8 or int(c.media_sub or 0)!=4: continue
            fs = wire.parse_wire(c.SerializeToString())
            uA=getf(fs,48503); nA=getf(fs,48504)
            uB=getf(fs,48506); nB=getf(fs,48507)
            snd = str(r.get("sender_uid") or "")
            peer = str(r.get("peer_uid") or r.get("group_id") or "")
            st["n"]+=1
            for lab,u in (("A",uA),("B",uB)):
                if u and u==snd: st[lab+"==sender"]+=1
                if u and u==peer: st[lab+"==peer"]+=1
            if len(rows)<5:
                rows.append((tag, r.get("msg_id"), "snd="+snd, "peer="+peer, "A="+nA+"/"+uA, "B="+nB+"/"+uB))
    print("[%s] %s" % (tag, dict(st)))
    for x in rows: print("   ", x)
scan(c2c_parser.SELECT_SQL, "c2c")
scan(group_exporter.SELECT_SQL, "group")
con.close()

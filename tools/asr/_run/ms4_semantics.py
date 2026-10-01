# -*- coding: utf-8 -*-
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
from msgdb.c2c import parser as c2c_parser
clear = ROOT / "data" / "decrypt" / "1605289411" / "nt_msg_clear.db"
KEY = (ROOT / "data" / "keys" / "1605289411.key").read_text(encoding="utf-8").strip()
con, _ = open_enc(clear, [KEY])
nm = {}
got = names.fetch_names(1605289411)
for uid, info in (got.get("people") or {}).items():
    if isinstance(info, dict):
        v = str(info.get("remark") or info.get("name") or "").strip()
        if v: nm[str(uid)] = v
print("name_map", len(nm))
stat = Counter(); pairs = Counter(); sample=[]
def getf(fs, num):
    for f in fs:
        if f.number == num and f.wire_type == 2:
            try: return f.raw_value.decode("utf-8","replace")
            except Exception: return ""
    return ""
def scan(sql, tag):
    cur = con.execute(sql); cols=[d[0] for d in cur.description]
    for raw in cur:
        r = dict(zip(cols, raw)); blob = r.get("blob")
        if not blob: continue
        try: cs=list(parse_40800(blob).contents)
        except Exception: continue
        for c in cs:
            if int(c.content_type or 0) != 8 or int(c.media_sub or 0) != 4: continue
            fs = wire.parse_wire(c.SerializeToString())
            uA=getf(fs,48503); nA1=getf(fs,48504); nA2=getf(fs,48505)
            uB=getf(fs,48506); nB1=getf(fs,48507); nB2=getf(fs,48508)
            snd = str(getattr(c,"sender_uid","") or r.get("sender_uid") or "")
            peer = str(r.get("peer_uid") or "")
            stat["rows"] += 1
            if uA and uA == snd: stat["A==sender"] += 1
            if uB and uB == snd: stat["B==sender"] += 1
            if uA and uA == peer: stat["A==peer"] += 1
            if uB and uB == peer: stat["B==peer"] += 1
            stat["A_nick_nonempty"] += 1 if nA1.strip() else 0
            stat["B_nick_nonempty"] += 1 if nB1.strip() else 0
            stat["A1==A2"] += 1 if nA1==nA2 else 0
            stat["B1==B2"] += 1 if nB1==nB2 else 0
            stat["A_in_namemap"] += 1 if uA in nm else 0
            stat["B_in_namemap"] += 1 if uB in nm else 0
            stat["A_nick_matches_map"] += 1 if (uA in nm and nA1.strip() and nm[uA]==nA1) else 0
            stat["B_nick_matches_map"] += 1 if (uB in nm and nB1.strip() and nm[uB]==nB1) else 0
            if len(sample) < 6:
                sample.append((tag, uA, nA1, nA2, uB, nB1, nB2, snd, peer))
scan(c2c_parser.SELECT_SQL, "c2c")
scan(group_exporter.SELECT_SQL, "group")
for k,v in stat.items(): print("%-22s %d" % (k,v))
for s in sample: print(s)
con.close()

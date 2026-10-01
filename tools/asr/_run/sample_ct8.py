# -*- coding: utf-8 -*-
import sys, json
from pathlib import Path
from collections import Counter
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
from core.sources._pack_extract import open_enc
from msgdb.proto.c2c_40800_parser import parse_40800
from msgdb.group import exporter as group_exporter
from msgdb.c2c import parser as c2c_parser
clear = ROOT / "data" / "decrypt" / "1605289411" / "nt_msg_clear.db"
KEY = (ROOT / "data" / "keys" / "1605289411.key").read_text(encoding="utf-8").strip()
con, _ = open_enc(clear, [KEY])

def sample(sql, tag, want, n=6):
    cur = con.execute(sql); cols = [d[0] for d in cur.description]
    got = 0
    for raw in cur:
        r = dict(zip(cols, raw)); blob = r.get("blob")
        if not blob: continue
        try: cs = list(parse_40800(blob).contents)
        except Exception: continue
        for c in cs:
            if int(c.content_type or 0) != 8: continue
            uid=(c.nc_uid_1 or c.nc_uid_2 or "").strip()
            nick=(c.nc_nickname_1 or c.nc_nickname_2 or "").strip()
            ref=(c.ref_f47713 or "").strip()
            ark=(c.reply_f48271 or "").strip()
            fname=(c.filename or "").strip()
            if want=="ark" and not (ark and not nick and not uid and not fname): continue
            if want=="empty" and not (not ark and not nick and not uid and not ref and not fname): continue
            got += 1
            print("----", tag, want, "msg_id", r.get("msg_id"), "mt", r.get("msg_type"))
            print("   uid=%r nick=%r ref=%r fname=%r" % (uid, nick, ref, fname))
            print("   ark=", ark[:400])
            break
        if got >= n: break
    return got

sample(c2c_parser.SELECT_SQL, "c2c", "ark", 4)
sample(group_exporter.SELECT_SQL, "group", "ark", 4)
sample(c2c_parser.SELECT_SQL, "c2c", "empty", 3)
sample(group_exporter.SELECT_SQL, "group", "empty", 3)
con.close()

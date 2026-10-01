# -*- coding: utf-8 -*-
import sys
from pathlib import Path
from collections import Counter
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
import sqlcipher3.dbapi2 as sc
from msgdb.proto.c2c_40800_parser import parse_40800
from msgdb.group import exporter as group_exporter
from msgdb.c2c import parser as c2c_parser

DB = ROOT / "data" / "decrypt" / "1605289411" / "nt_msg_clear.db"
KEY = (ROOT / "data" / "keys" / "1605289411.key").read_text(encoding="utf-8").strip()
con = sc.connect(str(DB), isolation_level=None)
for p in ("PRAGMA cipher_page_size = 4096;", "PRAGMA key = '%s';" % KEY,
          "PRAGMA kdf_iter = 4000;", "PRAGMA cipher_hmac_algorithm = HMAC_SHA1;",
          "PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;"):
    con.execute(p)

cat = Counter(); examples = {}
def scan(sql, tag):
    cur = con.execute(sql)
    for raw in cur:
        cols = [d[0] for d in cur.description]
        r = dict(zip(cols, raw))
        blob = r.get("blob")
        if not blob: continue
        try:
            res = parse_40800(blob)
            cs = list(res.contents)
        except Exception:
            continue
        for c in cs:
            if int(c.content_type or 0) != 8: continue
            uid = (c.nc_uid_1 or c.nc_uid_2 or "").strip()
            nick = (c.nc_nickname_1 or c.nc_nickname_2 or "").strip()
            fname = (c.filename or "").strip()
            ref = (c.ref_f47713 or "").strip()
            r48271 = (c.reply_f48271 or "").strip()
            if nick: k = "A_nick_card"
            elif uid and ref: k = "B_recall_uid_ref"
            elif uid: k = "C_uid_only"
            elif fname: k = "D_file_has_filename"
            elif r48271: k = "E_ark_reply_f48271"
            elif ref: k = "F_ref_only"
            else: k = "G_empty_shell"
            cat[(tag, k)] += 1
            examples.setdefault((tag, k), (r.get("msg_id"), uid, nick, fname[:20], ref[:30]))

scan(c2c_parser.SELECT_SQL, "c2c")
scan(group_exporter.SELECT_SQL, "group")
for k, v in sorted(cat.items()):
    print("%-6s %-24s %6d   ex=%s" % (k[0], k[1], v, examples.get(k)))
print("total ct8:", sum(cat.values()))
con.close()

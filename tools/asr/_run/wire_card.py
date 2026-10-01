# -*- coding: utf-8 -*-
"""wire-dump 真实名片 blob：找出昵称到底在哪个字段。"""
import sys, json
from pathlib import Path
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
import sqlcipher3.dbapi2 as sc
from msgdb.proto.c2c_40800_parser import parse_40800
from msgdb.proto import wire
from msgdb.proto import c2c_40800_pb2 as pb

DB = ROOT / "data" / "decrypt" / "1605289411" / "nt_msg_clear.db"
KEY = (ROOT / "data" / "keys" / "1605289411.key").read_text(encoding="utf-8").strip()

con = sc.connect(str(DB), isolation_level=None)
con.execute("PRAGMA cipher_page_size = 4096;")
con.execute(f"PRAGMA key = '{KEY}';")
con.execute("PRAGMA kdf_iter = 4000;")
con.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA1;")
con.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;")
print("count all:", con.execute('SELECT COUNT(*) FROM c2c_msg_table').fetchone()[0])
q = 'SELECT "40001" mid, "40011" mt, "40800" blob FROM c2c_msg_table WHERE "40011"=5 AND "40800" IS NOT NULL LIMIT 6'
rows = con.execute(q).fetchall()
print("card rows sample:", len(rows))

def val_of(f):
    if f.wire_type == 0:
        return f.value
    if f.wire_type == 2:
        try:
            s = f.raw_value.decode("utf-8")
            if all(31 < ord(ch) or ch in "\t\n" for ch in s) and s.strip():
                return f"str:{s}"
        except Exception:
            pass
        return f"bytes[{len(f.raw_value)}]:{f.raw_value[:12].hex()}"
    return f"raw:{f.raw_value.hex()[:24]}"

for mid, mt, blob in rows:
    res = parse_40800(blob)
    print("="*70)
    print("msg_id", mid, "msg_type", mt, "status", res.status, "n_contents", len(res.contents))
    if not res.contents:
        continue
    c = res.contents[0]
    print("content_type", c.content_type, "nc_uid_1", repr(c.nc_uid_1), "nc_nickname_1", repr(c.nc_nickname_1),
          "nc_nickname_2", repr(c.nc_nickname_2), "ref_msg.ct", c.ref_msg.content_type)
    raw = c.SerializeToString()
    fields = wire.parse_wire(raw)
    print("-- fields (num: value) --")
    for f in fields:
        print("   f%-6d w%d %s" % (f.number, f.wire_type, val_of(f)))
con.close()

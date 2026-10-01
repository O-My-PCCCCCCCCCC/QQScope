# -*- coding: utf-8 -*-
"""wire-dump 一批 bare ct8 blob，找 uid/昵称藏在哪。"""
import sys, json
from pathlib import Path
ROOT = Path(r"E:\01-项目\QQScope")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "nt_msg_db_util"))
import sqlcipher3.dbapi2 as sc
from msgdb.proto.c2c_40800_parser import parse_40800
from msgdb.group import exporter as group_exporter
from msgdb.proto import wire
from core.sources._pack_extract import build_media

DB = ROOT / "data" / "decrypt" / "1605289411" / "nt_msg_clear.db"
KEY = (ROOT / "data" / "keys" / "1605289411.key").read_text(encoding="utf-8").strip()
con = sc.connect(str(DB), isolation_level=None)
for p in ("PRAGMA cipher_page_size = 4096;", f"PRAGMA key = '{KEY}';",
          "PRAGMA kdf_iter = 4000;", "PRAGMA cipher_hmac_algorithm = HMAC_SHA1;",
          "PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;"):
    con.execute(p)

def val_of(f):
    if f.wire_type == 0: return f.value
    if f.wire_type == 2:
        try:
            s = f.raw_value.decode("utf-8")
            if s.strip() and all(ch.isprintable() or ch in "\t\n" for ch in s):
                return "str:" + s
        except Exception: pass
        return f"bytes[{len(f.raw_value)}]:{f.raw_value[:10].hex()}"
    return f"raw:{f.raw_value.hex()[:20]}"

cur = con.execute(group_exporter.SELECT_SQL)
found = 0
for raw in cur:
    cols = [d[0] for d in cur.description]
    r = dict(zip(cols, raw))
    blob = r.get("blob")
    if not blob: continue
    res = parse_40800(blob)
    if not res.contents: continue
    cs = list(res.contents)
    mo = build_media(cs)
    if not (mo and mo.get("kind")=="card" and str(mo.get("fallback"))=="[名片]"):
        continue
    cand = [c for c in cs if int(c.content_type or 0)==8]
    if not cand: continue
    c = cand[0]
    raw_b = c.SerializeToString()
    fields = wire.parse_wire(raw_b)
    strs = [(f.number, val_of(f)) for f in fields]
    if any(isinstance(v,str) and (v.startswith("str:u_") or v.startswith("str:U_")) for _,v in strs):
        pass
    found += 1
    print("="*70)
    print("msg_id", r.get("msg_id"), "gq", r.get("group_qq"), "content_type", c.content_type,
          "nc_uid_1", repr(c.nc_uid_1), "nc_nickname_1", repr(c.nc_nickname_1))
    for num, v in strs:
        print("   f%-7d %s" % (num, v))
    if found >= 8: break
con.close()

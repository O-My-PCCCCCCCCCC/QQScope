# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store, media, paths
con = store.connect()
r = con.execute("SELECT id, media FROM messages WHERE id=171786").fetchone()
m = json.loads(r["media"])
print("resolve ->", media.resolve(1605289411, m))
p = media.resolve(1605289411, m)
from pathlib import Path
pp = Path(str(p))
print("exists:", pp.exists())
print("data root:", getattr(paths, "DATA", None))
print("media dir candidates:")
for k in dir(paths):
    if "MEDIA" in k.upper() or "DATA" in k.upper():
        print("   ", k, getattr(paths, k))
con.close()

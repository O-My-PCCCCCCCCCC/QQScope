# -*- coding: utf-8 -*-
"""重测新配置（同一批 20 条，修掉看门狗误杀后）。"""
import json, sys, time
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store, voice  # noqa: E402

QQ = 1605289411
IDS = [48424, 48463, 83082, 84282, 92250, 97837, 112343, 117200, 132363, 134318,
       135508, 178863, 181402, 189340, 193182, 203840, 206185, 207050, 207943, 208066]
con = store.connect()
for mid in IDS:
    r = con.execute("SELECT media FROM messages WHERE id=?", (mid,)).fetchone()
    if not r:
        continue
    m = json.loads(r["media"] or "{}")
    for k in list(m.keys()):
        if k.startswith("voice_"):
            m.pop(k)
    con.execute("UPDATE messages SET media=? WHERE id=?", (json.dumps(m, ensure_ascii=False), mid))
con.commit()
con.close()
t0 = time.monotonic()
rb = voice.run(QQ, workers=16, threads=2, vad_skip_ratio=0.12, with_timestamps=False, only_ids=IDS)
wb = time.monotonic() - t0
print("B_NEW_FIXED wall=%.1fs ok=%s failed=%s timeouts=%s speed=%.2fx" % (
    wb, rb.get("transcribed"), rb.get("failed"), rb.get("timeouts"), rb.get("speed") or 0), flush=True)
print("B_JSON " + json.dumps({"wall": round(wb, 1), "ok": rb.get("transcribed"),
    "failed": rb.get("failed"), "timeouts": rb.get("timeouts"), "speed": rb.get("speed"),
    "old_wall": 320.4, "speedup": round(320.4 / wb, 2) if wb else None}, ensure_ascii=False), flush=True)
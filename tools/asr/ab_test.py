# -*- coding: utf-8 -*-
"""A/B：同一批 20 条语音，旧配置 vs 新配置（含单文件看门狗）。"""
import json, sys, time
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store, voice  # noqa: E402

QQ = 1605289411
IDS = [48424, 48463, 83082, 84282, 92250, 97837, 112343, 117200, 132363, 134318,
       135508, 178863, 181402, 189340, 193182, 203840, 206185, 207050, 207943, 208066]

con = store.connect()
snap = {}
for mid in IDS:
    r = con.execute("SELECT media FROM messages WHERE id=?", (mid,)).fetchone()
    if r:
        snap[mid] = r["media"]
con.close()
audio = 0.0
for mid, js in snap.items():
    try: audio += float(json.loads(js).get("duration") or 0)
    except Exception: pass
print("A/B jobs=%d 音频=%.0fs" % (len(snap), audio), flush=True)

def restore():
    con = store.connect()
    for mid, js in snap.items():
        con.execute("UPDATE messages SET media=? WHERE id=?", (js, mid))
    con.commit(); con.close()

def line(tag, wall, r):
    print("%s wall=%.1fs ok=%s failed=%s timeouts=%s speed=%.2fx" % (
        tag, wall, r.get("transcribed"), r.get("failed"), r.get("timeouts"), r.get("speed") or 0), flush=True)

restore()
t0 = time.monotonic()
ra = voice.run(QQ, workers=4, threads=8, vad_skip_ratio=0, with_timestamps=True, only_ids=IDS)
wa = time.monotonic() - t0
line("A_OLD", wa, ra)

restore()
t0 = time.monotonic()
rb = voice.run(QQ, workers=16, threads=2, vad_skip_ratio=0.12, with_timestamps=False, only_ids=IDS)
wb = time.monotonic() - t0
line("B_NEW", wb, rb)

print("AB_JSON " + json.dumps({"audio_sec": audio,
    "old": {"wall": round(wa,1), "ok": ra.get("transcribed"), "failed": ra.get("failed"),
            "timeouts": ra.get("timeouts"), "speed": ra.get("speed")},
    "new": {"wall": round(wb,1), "ok": rb.get("transcribed"), "failed": rb.get("failed"),
            "timeouts": rb.get("timeouts"), "speed": rb.get("speed")},
    "speedup": round(wa/wb, 2) if wb else None}, ensure_ascii=False), flush=True)
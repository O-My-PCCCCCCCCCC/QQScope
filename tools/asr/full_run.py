# -*- coding: utf-8 -*-
"""全量重跑（新配置）：重置所有 voice 行 -> 16 worker × 2 线程 + VAD 预筛 -> 记录墙钟。"""
import json, sys, time
sys.path.insert(0, r"E:\01-项目\QQScope")
from core import store, voice  # noqa: E402

QQ = 1605289411
con = store.connect()
rows = con.execute("SELECT id, media FROM messages WHERE account_qq=? AND json_extract(media,'$.kind')='voice'", (QQ,)).fetchall()
reset = 0
for r in rows:
    m = json.loads(r["media"] or "{}")
    changed = False
    for k in list(m.keys()):
        if k.startswith("voice_"):
            m.pop(k); changed = True
    if changed:
        con.execute("UPDATE messages SET media=? WHERE id=?", (json.dumps(m, ensure_ascii=False), r["id"]))
        reset += 1
con.commit()
con.close()
print("reset voice rows: %d / %d" % (reset, len(rows)), flush=True)

def prog(stage, done, total, cur):
    if done % 25 == 0 or stage in ("准备", "完成"):
        print("[%s] %d/%d %s" % (stage, done, total, str(cur)[:40]), flush=True)

t0 = time.monotonic()
res = voice.run(QQ, workers=16, threads=2, vad_skip_ratio=0.12, with_timestamps=False, progress=prog)
wall = time.monotonic() - t0
print("FULL_RUN wall=%.1fs (%.1f min)" % (wall, wall / 60), flush=True)
print("FULL_JSON " + json.dumps({k: res.get(k) for k in
      ("total", "transcribed", "failed", "timeouts", "missing_files", "lang_mismatch",
       "audio_seconds", "speed", "elapsed")}, ensure_ascii=False), flush=True)
print("FINAL_STATS " + json.dumps(voice.stats(QQ), ensure_ascii=False), flush=True)
# -*- coding: utf-8 -*-
"""预筛判据标定：读已有 4 组真实标注（vad_calib.json / vad_calib2.json）算准确率。"""
import json, sys
from pathlib import Path
RUN = Path(r"E:\01-项目\QQScope\tools\asr\_run")
ok, bad = [], []
for name in ("vad_calib.json", "vad_calib2.json"):
    d = json.loads((RUN / name).read_text(encoding="utf-8"))
    for x in d.get("ok") or []:
        x["src"] = name; ok.append(x)
    for x in d.get("bad") or []:
        x["src"] = name; bad.append(x)
# bad 里混了两类：长音乐/噪声(sec>=90，应跳) 与 短语音幻觉(suspect, sec<90，属于语音)
music = [x for x in bad if x["sec"] >= 90]
suspect_speech = [x for x in bad if x["sec"] < 90]
print("ok samples=%d (min ratio=%.4f max=%.4f)" % (len(ok), min(x["speech_ratio"] for x in ok), max(x["speech_ratio"] for x in ok)))
print("music/empty long samples=%d (max ratio=%.4f)" % (len(music), max(x["speech_ratio"] for x in music)))
print("suspect short speech=%d (min ratio=%.4f)" % (len(suspect_speech), min((x["speech_ratio"] for x in suspect_speech), default=0)))
for thr in (0.02, 0.05, 0.12, 0.20):
    fp = [x["id"] for x in ok if x["speech_ratio"] < thr]
    tp = [x["id"] for x in music if x["speech_ratio"] < thr]
    sk = [x["id"] for x in suspect_speech if x["speech_ratio"] < thr]
    print("thr=%.2f  长音乐拦下 %d/%d   真语音(ok)误杀 %d/%d %s   短suspect误杀 %d/%d %s" % (
        thr, len(tp), len(music), len(fp), len(ok), fp[:6], len(sk), len(suspect_speech), sk[:6]))
print("--- ok samples with ratio<0.12 (会被误杀的真语音) ---")
for x in sorted(ok, key=lambda v: v["speech_ratio"]):
    if x["speech_ratio"] < 0.12:
        print("   id=%s sec=%.1f ratio=%.4f src=%s" % (x["id"], x["sec"], x["speech_ratio"], x["src"]))

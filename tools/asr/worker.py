# -*- coding: utf-8 -*-
"""QQScope · ASR worker（必须在 tools/asr/.venv 里跑）

把一批 QQ SILK 语音转成文字，逐行写 JSONL（父进程 core/voice.py 边跑边读）。

链路（刻意绕开 PyAV，Lead 实测）：
    QQ .amr（其实是 1 字节 + '#!SILK_V3'） --pilk--> 16kHz 单声道 WAV
    --标准库 wave + numpy.frombuffer(int16)/32768--> float32 ndarray
    --faster-whisper transcribe(ndarray, language='zh', initial_prompt=中文)--> 文字

用法：
    python worker.py --jobs jobs.json --out out.jsonl
        [--model small] [--threads 6] [--int8/--no-int8] [--vad/--no-vad]

jobs.json: [{"id": 123, "path": "C:\\...\\xxx.amr"}, ...]
输出每行立即 flush：
    {"id":123,"ok":true,"text":"...","lang":"zh","seconds":3.2,"elapsed":2.1,"engine":"..."}
    {"id":123,"ok":false,"error":"解码失败：...","stage":"decode"}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

SAMPLE_RATE = 16000
# 实测：initial_prompt 会让部分音频输出空串/原样复读提示词，默认关闭；
# language="zh" 已足够把语种压在中文。需要时可 --prompt 开启。
DEFAULT_PROMPT = ""
ENGINE = "faster-whisper"

# 高频错字纠正（只改明显错误的，拿不准的保持原样）
FIXES = (
    ("謝緒", "情绪"), ("谢绪", "情绪"),
    ("謝維", "思维"), ("谢维", "思维"),
    ("遊戲", "游戏"), ("發展", "发展"),
)

OPENCC = None
try:
    import opencc  # type: ignore

    OPENCC = opencc.OpenCC("t2s")
except Exception:  # noqa: BLE001
    OPENCC = None


def _decode_av(path: str):
    """非 SILK 音频（mp3/ogg/m4a/wav/mp4）用 PyAV 自己解成 16k 单声道 float32。

    注意：这里只借 PyAV 做解码，不把它交给 faster-whisper（那个链路才是坑）。
    """
    import av
    import numpy as np

    try:
        container = av.open(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"不是 SILK，PyAV 也打不开：{exc}") from exc
    try:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("不是 SILK，且文件里没有音频流")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        chunks = []
        for frame in container.decode(stream):
            for new_frame in resampler.resample(frame):
                chunks.append(new_frame.to_ndarray().astype("float32").reshape(-1) / 32768.0)
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")
    finally:
        container.close()
    if audio.size == 0:
        raise ValueError("解码后音频为空")
    return audio


def load_audio(silk_path: str, workdir: Path):
    """SILK -> (float32 ndarray, 采样率)。非 SILK 的音频文件走 PyAV 回退。"""
    import numpy as np
    import pilk

    raw = Path(silk_path).read_bytes()
    idx = raw.find(b"#!SILK")
    if idx < 0:
        return _decode_av(silk_path), SAMPLE_RATE
    tmp_silk = workdir / (Path(silk_path).stem + ".silk")
    tmp_wav = workdir / (Path(silk_path).stem + ".wav")
    tmp_silk.write_bytes(raw[idx:])
    pilk.silk_to_wav(str(tmp_silk), str(tmp_wav), rate=SAMPLE_RATE)
    with wave.open(str(tmp_wav), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    try:
        tmp_silk.unlink()
        tmp_wav.unlink()
    except OSError:
        pass
    if sampwidth != 2:
        raise ValueError(f"WAV 位深不是 16bit（{sampwidth * 8}bit），无法直接喂模型")
    audio = np.frombuffer(frames, dtype=np.int16).astype("float32") / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if audio.size == 0:
        raise ValueError("解码后音频为空")
    return audio, rate


def looks_hallucinated(text: str) -> bool:
    """识别重复幻觉：音乐/噪声上 whisper 常输出同一小片段反复。"""
    t = "".join((text or "").split())
    if len(t) < 20:
        return False
    from collections import Counter
    for n in (1, 2, 3, 4):
        grams = [t[i:i + n] for i in range(0, len(t) - n + 1, n)]
        if len(grams) < 8:
            continue
        top = Counter(grams).most_common(1)[0]
        if top[1] >= 8 and top[1] * n >= 0.5 * len(t):
            return True
    return False


_VAD_OPTS = None


def speech_ratio(audio) -> float:
    """便宜的预筛判据：VAD 判定为语音的时长占比。"""
    global _VAD_OPTS
    from faster_whisper.vad import get_speech_timestamps, VadOptions

    if _VAD_OPTS is None:
        _VAD_OPTS = VadOptions()
    try:
        ts = get_speech_timestamps(audio, _VAD_OPTS, sampling_rate=SAMPLE_RATE)
    except Exception:  # noqa: BLE001
        return 1.0
    speech = sum(t["end"] - t["start"] for t in ts)
    return speech / max(1, audio.size)


def clean_text(text: str) -> str:
    t = (text or "").strip()
    if OPENCC is not None and t:
        try:
            t = OPENCC.convert(t)
        except Exception:  # noqa: BLE001
            pass
    for a, b in FIXES:
        if a in t:
            t = t.replace(a, b)
    return " ".join(t.split())


def main() -> int:
    ap = argparse.ArgumentParser(description="QQScope ASR worker")
    ap.add_argument("--jobs", required=True, help="jobs.json（[{id,path}]）")
    ap.add_argument("--out", required=True, help="结果 JSONL 输出路径")
    ap.add_argument("--model", default="small")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--compute-type", default="int8")
    ap.add_argument("--no-vad", action="store_true", help="关闭 VAD（默认开启）")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="initial_prompt（默认空，实测开了会伤识别）")
    ap.add_argument("--temperature", type=float, default=0.0,
                    help="采样温度；0 = 关闭温度回退（实测快 3~5 倍且文本更干净）")
    ap.add_argument("--with-timestamps", action="store_true",
                    help="保留逐段时间戳（默认关闭，更快）")
    ap.add_argument("--vad-skip-ratio", type=float, default=0.12,
                    help="解码后 VAD 语音占比低于此值直接判 empty，不进 whisper（0=关闭预筛）")
    ap.add_argument("--lang-force-threshold", type=float, default=0.75,
                    help="自动检测到非中文且置信度低于此值时，强制按中文重跑一次")
    args = ap.parse_args()

    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
    out = open(args.out, "a", encoding="utf-8", buffering=1)

    def emit(obj: dict) -> None:
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out.flush()

    if not jobs:
        out.close()
        return 0

    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # noqa: BLE001
        emit({"id": -1, "ok": False, "error": f"加载 faster-whisper 失败：{exc}", "stage": "import"})
        out.close()
        return 3

    try:
        model = WhisperModel(args.model, device=args.device,
                             compute_type=args.compute_type,
                             cpu_threads=max(1, args.threads))
    except Exception as exc:  # noqa: BLE001
        emit({"id": -1, "ok": False, "error": f"加载模型 {args.model} 失败：{exc}", "stage": "model"})
        out.close()
        return 3

    engine = f"{ENGINE}-{args.model}-{args.compute_type}"
    # 心跳：告诉父进程「模型已加载」，避免冷启动被误判成卡死
    emit({"id": 0, "event": "ready", "model": args.model, "engine": engine})
    workdir = Path(tempfile.mkdtemp(prefix="qqscope_asr_"))

    for job in jobs:
        mid = int(job.get("id") or 0)
        path = str(job.get("path") or "")
        t0 = time.monotonic()
        try:
            audio, rate = load_audio(path, workdir)
        except FileNotFoundError:
            emit({"id": mid, "ok": False, "error": "文件不存在", "stage": "missing"})
            continue
        except Exception as exc:  # noqa: BLE001
            emit({"id": mid, "ok": False, "error": f"解码失败：{exc}", "stage": "decode"})
            continue

        seconds = round(audio.size / float(rate or SAMPLE_RATE), 2)
        forced = False

        if args.vad_skip_ratio > 0:
            ratio = speech_ratio(audio)
            if ratio < args.vad_skip_ratio:
                emit({"id": mid, "ok": False,
                      "error": "VAD 预筛：语音占比 %.1f%% < %.0f%%（音乐/静音跳过）"
                               % (ratio * 100, args.vad_skip_ratio * 100),
                      "stage": "empty", "seconds": seconds,
                      "elapsed": round(time.monotonic() - t0, 2), "engine": engine})
                continue

        def _run(language):
            segs, inf = model.transcribe(
                audio,
                language=language,
                initial_prompt=(args.prompt or None),
                beam_size=1,
                temperature=args.temperature,
                without_timestamps=not args.with_timestamps,
                vad_filter=not args.no_vad,
                condition_on_previous_text=False,
            )
            return [s.text for s in segs], inf

        try:
            # 先自动检测语种：跑偏的（尤其中文被识别成日文）要能标记出来
            parts, info = _run(None)
            lang = getattr(info, "language", "") or ""
            prob = float(getattr(info, "language_probability", 0.0) or 0.0)
            if lang and lang != "zh" and prob < args.lang_force_threshold:
                # 低置信度跑偏 -> 强制中文重跑一次，避免把中文当外语
                parts, info = _run("zh")
                lang = "zh"
                forced = True
        except Exception as exc:  # noqa: BLE001
            emit({"id": mid, "ok": False, "error": f"识别失败：{exc}", "stage": "asr",
                  "seconds": seconds})
            continue

        raw_text = " ".join(parts).strip()
        text = clean_text(raw_text)
        # 防提示词复读：结果几乎等于 prompt 时按空结果处理
        if args.prompt and text and text.strip().strip("。.,，") == args.prompt.strip().strip("。.,，"):
            text = ""
        elapsed = round(time.monotonic() - t0, 2)
        if not text:
            emit({"id": mid, "ok": False, "error": "识别结果为空", "stage": "empty",
                  "lang": lang, "seconds": seconds, "elapsed": elapsed, "engine": engine})
            continue
        if looks_hallucinated(text):
            emit({"id": mid, "ok": False, "error": "疑似重复幻觉（音乐/噪声），已丢弃",
                  "stage": "suspect", "lang": lang, "seconds": seconds,
                  "elapsed": elapsed, "engine": engine})
            continue
        emit({"id": mid, "ok": True, "text": text, "lang": lang or "zh",
              "lang_forced": forced, "seconds": seconds, "elapsed": elapsed, "engine": engine})

    try:
        for f in workdir.iterdir():
            f.unlink()
        workdir.rmdir()
    except OSError:
        pass
    out.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
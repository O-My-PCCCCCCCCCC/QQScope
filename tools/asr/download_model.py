# -*- coding: utf-8 -*-
"""下载 faster-whisper small 模型（国内走 hf-mirror，禁用 Xet 走普通 HTTP）。"""
import os

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from huggingface_hub import hf_hub_download, snapshot_download  # noqa: E402

print("HF_ENDPOINT =", os.environ.get("HF_ENDPOINT"),
      "DISABLE_XET =", os.environ.get("HF_HUB_DISABLE_XET"), flush=True)
path = snapshot_download("Systran/faster-whisper-small", force_download=True)
print("MODEL_PATH =", path, flush=True)
print("MODEL_BIN =", hf_hub_download("Systran/faster-whisper-small", "model.bin"), flush=True)
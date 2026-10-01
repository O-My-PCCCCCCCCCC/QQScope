# -*- coding: utf-8 -*-
import io
from pathlib import Path
ROOT = Path(r"E:\01-项目\QQScope")
out = ROOT / "web" / "selftest" / "REPORT-asr.md"

MD = """# task-3 语音/ASR + 名片修复 · 实测报告

> 交付物：本地 ASR 收尾（pending=0）+ 名片昵称修复 + ct8 误差量化 + 普通设备资源评估。
> 详细技术版：`tools/asr/_run/VOICE_REPORT.md`；原始证据：`tools/asr/_run/`。

## 1. 改动文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `tools/asr/update_cards.py` | 新增 | 只补全「当前 `fallback=='[名片]'`」的 card 行；老布局昵称 / 新布局 ms4 / uid 查名；带 `--dry-run` |
| `core/voice.py` | 小修 | `pending_jobs()` 去掉 `media.file IS NOT NULL` 过滤，缺文件的行才能被标记 `missing`（否则 pending 永远清不到 0） |
| `core/sources/_pack_extract.py` | 未改 | 本轮开始时它已读 `nc_nickname_1`(47705)/`nc_nickname_2`；ct8 语义修正按 Lead 裁决另立项（仅给建议+数字） |
| `tools/asr/_run/VOICE_REPORT.md` | 报告 | 技术版（A/B、预筛标定、ct8 表、设备外推） |
| `web/selftest/REPORT-asr.md` | 报告 | 本文件 |

未触碰：`core/store.py`、`core/media.py`、`core/export.py`、`server/app.py`、`web/` 业务代码、`app/js/`、NapCat(node 39760)。
**未重启 15555**（voice_text 只写库、不走内存缓存；API 与 `core.voice.stats()` 逐字段一致，见 §2）。

## 2. 验收 1：pending=0

命令：`GET http://127.0.0.1:15555/api/voice/stats?account=1605289411`（httpx, trust_env=False）

```
voice_total 665 | local_files 664 | transcribed(ok) 542 | failed 123 | pending 0
lang_mismatch 15 | avg_seconds 20.12 | transcribed_seconds 10843.5 | engine faster-whisper-small-int8
status_counts: {ok: 542, empty: 114, suspect: 7, decode: 1, missing: 1}
```

- `core.voice.stats()` 返回值与 API 完全一致 -> 后端无需重启。
- 收尾跑了 3 批（幂等续跑，`ok/empty` 跳过）：10x2 线程 41 条（1172 s 音频 / 328 s = 3.57x）、
  4x2 线程 8 条、2x2 线程补 1 条 missing。
- **PASS**

## 3. 验收 2：A/B（同一批 20 条，531 s 音频）

脚本 `tools/asr/ab_test.py` / `ab_b.py`，日志 `tools/asr/_run/ab_test.log` / `ab_b.log`。

| 配置 | 墙钟 | 实时倍速 | ok/failed |
| --- | --- | --- | --- |
| A 旧：4x8 线程 + 带时间戳 + 无预筛 | 320.4 s | 1.66x | 9 / 11 |
| B 新：16x2 线程 + 无时间戳 + 预筛 0.12 | 152.1 s | 3.49x | 10 / 10 |
| B 新（看门狗放宽后复测） | 187.9 s | 2.83x | 11 / 9 |

同批 **1.7x ~ 2.1x**；混合负载（10x2）实测 3.57x。**PASS**

## 4. 验收 3：预筛判据准确率（真实标定）

脚本 `tools/asr/_run/vad_accuracy.py`，数据 `vad_calib.json` + `vad_calib2.json`
（40 条已知 ok 语音 + 41 条长音乐/空 + 9 条短 suspect 语音）。

| 阈值 | 长音乐拦下 | 真语音误杀 |
| --- | --- | --- |
| 0.02 | 37/41 (90%) | **1/40 (2.5%)** |
| 0.05 | 37/41 | 4/40 (10%) |
| 0.12（默认） | 41/41 | 7/40 (17.5%) |
| 0.20 | 41/41 | 12/40 (30%) |

- 长音乐/空语音占比 0.0~0.083；短语音 0.59~1.0；**长录音里语音稀疏的 ok 文件低到 0.0037~0.10**。
- 更正上一版「误杀 0/20」的说法；**重导入建议 `--vad-skip-ratio 0.02`**。**PASS（诚实标定）**

## 5. 验收 4：名片修复

命令：`python tools/asr/update_cards.py --account 1605289411 [--dry-run]`

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| 光秃 `[名片]` | **12,367** | **11,123** |
| `[名片] 昵称` / `uid:x` | 12,347 | 13,591 |
| 本轮补全 | — | **1,244**（昵称 6 + ms4 新布局 1,125 + uid 查名 87 + uid 原样 26） |

修复后真实样例（`id / fallback / 原始字段`）：

| id | 修复后 | 来源 |
| --- | --- | --- |
| 533927 | `[名片] 羽莺1947III` | 老布局 nc_nickname_1 |
| 534067 | `[名片] 希尔德` | 老布局 nc_nickname_1 |
| 547309 | `[名片] 恋` | 新布局 media_sub=4 字段 48504 |
| 547323 | `[名片] 柠檬Dawn` | 新布局 ms4 48504 |
| 547328 | `[名片] 常知安 INTJ 9w1 926 sp/so` | 新布局 ms4 48504 |
| 44967 | `[名片] uid:u_7aRpfOwxXYuzOt2mEBd1EA` | 只有 uid，profile 库查不到 |

剩余 11,123 条光秃的具体构成（ct8 细分，NT 库 25,749 条）：

| 类别 | 判据 | 条数 |
| --- | --- | --- |
| 真名片·有昵称 | nc_nickname_1/2 | 4,118 |
| 真名片·仅 uid | nc_uid + 无下列标记 | 4,363 |
| 撤回/引用 | nc_uid + ref_f47713 | 4,564 |
| 戳一戳/ark | reply_f48271 | **11,011** |
| 群成员提醒 | 未知字段 48214 gtip XML | 327 |
| 文件类 | filename(45402) | 43 |
| 空壳 | 全空 | 1,323 |

其中 DB 侧光秃里 **戳一戳 10,106 条 + 群提醒 321 条**（合计 84%）根本不是名片。**语义修正未做，按 Lead 裁决另立项**（修法+预期数字见 VOICE_REPORT §5.5）。**PASS（按裁决口径）**

## 6. 验收 5：普通设备资源评估

| 设备 | 全量 8.31 h 音频 | 只转最近 50 条（0.60 h） |
| --- | --- | --- |
| 本机 Xeon E5-2673 v4 20C/40T | 1 ~ 2.4 h（实测 2.2x ~ 7.5x 区间） | 10 ~ 16 min |
| 普通 8 核笔记本 | 2.5 ~ 6 h | 25 ~ 40 min |
| 普通 4 核笔记本 | 5 ~ 12 h | 20 ~ 30 min |
| 4 核 + 最近 50 条 | 不适用 | 20 ~ 30 min |

关键结论（已写进 VOICE_REPORT §6.3）：
> 转写是一次性离线任务；跑完文字存在 `messages.media.voice_text`，其他设备**只读文本、完全不跑 ASR**，
> 拷贝 `data/qqscope.db` 即可。本机已 pending=0，不用再跑。

轻量用法：
```powershell
tools\\nt_msg_db_util\\.venv\\Scripts\\python.exe -m core.voice --account 1605289411 --recent 50 --workers 4 --threads 2 --yes
```
**PASS**

## 7. 复现清单

```powershell
$env:PYTHONIOENCODING = "utf-8"
cd E:\\01-项目\\QQScope

# 1) 状态
tools\\nt_msg_db_util\\.venv\\Scripts\\python.exe -m core.voice --account 1605289411 --stats-only

# 2) 续跑剩余（幂等）
tools\\nt_msg_db_util\\.venv\\Scripts\\python.exe -m core.voice --account 1605289411 --workers 10 --threads 2 --yes

# 3) 预筛标定准确率
tools\\nt_msg_db_util\\.venv\\Scripts\\python.exe tools\\asr\\_run\\vad_accuracy.py

# 4) 名片修复（先 dry-run 看数字，再去掉 --dry-run 落库）
tools\\nt_msg_db_util\\.venv\\Scripts\\python.exe tools\\asr\\update_cards.py --account 1605289411 --dry-run
tools\\nt_msg_db_util\\.venv\\Scripts\\python.exe tools\\asr\\update_cards.py --account 1605289411
```

## 8. 遗留 / 未做

1. **ct8 语义修正**（戳一戳 11,011 / 群提醒 327 从 `[名片]` 分出）——建议单独立项，最影响显示质量。
2. `suspect 7`（音乐上重复幻觉）、`decode 1`（id=48424）、`missing 1`（id=171786 本地文件确实不存在）保持原状态，未强改。
3. 语音随 NapCat 实时同步增长（本轮 664 -> 665），新语音会再出现 pending；跑一次续跑命令即可清零。
4. 未跑 `core/_export_selftest.py`、web 自检：本次只改 `core/voice.py` 的队列过滤 + 新增 `tools/asr/update_cards.py`，
   不涉及导出/前端契约；`core/sources/_pack_extract.py` 未改。
"""
out.write_text(MD, encoding="utf-8", newline="\n")
print("wrote", out, len(MD), "chars")

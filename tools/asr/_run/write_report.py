# -*- coding: utf-8 -*-
import io
from pathlib import Path
ROOT = Path(r"E:\01-项目\QQScope")
out = ROOT / "tools" / "asr" / "_run" / "VOICE_REPORT.md"

MD = """# QQScope · 本地语音转文字（ASR）· task-3 收尾报告

- 时间：2026-10-01（task-3，asr-fixer 收尾）
- 机器：Intel Xeon E5-2673 v4，20 核 / 40 线程，64 GB，纯 CPU（int8）
- 引擎：faster-whisper **small + int8**，worker = `tools/asr/worker.py`（pilk 解 SILK -> 16k WAV -> numpy -> whisper）
- 账号：1605289411；主库：`data/qqscope.db`（本次只做事务写，未删/未覆盖）

## 0. 结论（TL;DR）

- `GET /api/voice/stats?account=1605289411` 最终 **pending = 0**；`core.voice.stats()` 与 API 返回值逐字段一致（说明 voice_text 只写库、不走内存缓存，**不需要重启 15555**）。
- 语音 665 条：**ok 542 / empty 114 / suspect 7 / decode 1 / missing 1**（failed 合计 123）。
- 名片：光秃 `[名片]` **12367 -> 11123**，补全 **1244** 条（老布局昵称 6、新布局 ms4 1125、uid 查名 87、uid 原样 26）。
- ct=8 里混着「戳一戳 / 群成员提醒 / 撤回 / 文件」等非名片元素，共 2.5 万条；已量化成表（§5.4），**本次按 Lead 裁决未改 `_classify()` 语义**，建议单独立项。

---

## 1. 最终进度（真实数字）

口径：`GET /api/voice/stats?account=1605289411`（权威），并用 `core.voice.stats()` 复核。

| 指标 | 数值 |
| --- | --- |
| voice_total | 665 |
| local_files | 664（1 条本地文件确实不存在 -> missing） |
| **ok（有 voice_text）** | **542** |
| failed（空/疑似/解码失败/缺文件） | 123 |
| **pending** | **0** |
| lang_mismatch（非 zh） | 15 |
| ok 音频时长 | 11,626 s（3.23 h） |
| 全部语音音频时长 | 29,901 s（8.31 h） |

status_counts：

| status | 条数 | 说明 |
| --- | --- | --- |
| ok | 542 | 有文字 |
| empty | 114 | VAD 预筛判为无语音 / whisper 输出空 |
| suspect | 7 | 疑似重复幻觉（音乐上常出现） |
| decode | 1 | 解码失败 |
| missing | 1 | 本地文件不存在（id=171786，media.file=null） |

收尾过程（三批，幂等续跑，`ok/empty` 自动跳过）：

| 批次 | 起点 | 配置 | 结果 | 墙钟 | 音频 | 有效倍速 |
| --- | --- | --- | --- | --- | --- | --- |
| 第 1 批 | 34 pending + 1 decode + 7 suspect | 10 worker x 2 线程 | ok 19 / failed 22 | 328 s | 1,172 s | **3.57x** |
| 第 2 批 | 1 pending + 1 decode + 6 suspect | 4 worker x 2 线程 | ok 0 / failed 8 | 215 s | 482 s | 2.24x |
| 第 3 批 | 1 missing（file=null，之前永远进不了队列） | 2 worker x 2 线程 | missing 1 | 243 s | — | — |

> 第 3 批前修了一个真 bug：`core/voice.py::pending_jobs()` 用 `media.file IS NOT NULL` 过滤，
> 导致「本地文件缺失」的语音永远不会被标记，`pending` 永远清不到 0。已改为不过滤 file，
> 由 `_run_locked` 的 missing 分支如实写 `voice_status='missing'`。

---

## 2. A/B（同一批 20 条，音频 531 s）

脚本：`tools/asr/ab_test.py`（日志 `_run/ab_test.log`）、`tools/asr/ab_b.py`（日志 `_run/ab_b.log`）。

| 配置 | 墙钟 | 实时倍速 | 结果 |
| --- | --- | --- | --- |
| A 旧：4 worker x 8 线程、带时间戳、无预筛 | 320.4 s | **1.66x** | ok 9 / failed 11 |
| B 新：16 worker x 2 线程、无时间戳、VAD 预筛 0.12 | 152.1 s | **3.49x** | ok 10 / failed 10 |
| B 新（看门狗放宽后复测） | 187.9 s | **2.83x** | ok 11 / failed 9 |
| 本次收尾混合负载（10x2） | 328 s | **3.57x** | 1,172 s 音频 |

- 同批结论：**新配置比旧配置快 1.7x ~ 2.1x**。
- 口径说明：`worker.py` 里 `beam_size=1`、`temperature=0`、`condition_on_previous_text=False`、
  `without_timestamps` 默认值本来就已内置；A/B 真正对比的是
  「4x8 线程 + 带时间戳 + 无预筛」 vs 「16x2 线程 + 无时间戳 + VAD 预筛」。
- 混合负载差异很大（同一套配置观测到 2.2x ~ 7.5x）：音乐/长静音占比越高、预筛收益越大。

## 3. 线程超订与 worker 建议

- 旧配置 4 worker x 8 线程 = 32 线程；10 worker x 8 线程 = 80 线程挤 20 核 -> 明显超订，吞吐反而不涨。
- 16 worker x 2 线程 = 32 线程，吞吐上来了，但 16 个进程同时冷加载 small(int8) 模型
  会争内存带宽，2 s 小文件单条 wall 涨到 34~85 s（看门狗误杀过，已把单文件超时放宽到
  `max(180, 10x时长, 上限600s)` + ready 心跳）。
- **建议：稳态用 `--workers 8~10 --threads 2`**（本次收尾 10x2 实测 3.57x，且没有拖垮 15555）。
- 全量重跑前先看 `GET /api/voice/stats` 的 pending，别两套跑法叠着跑（`tools/asr/_run/asr.lock` 互斥）。

## 4. 长音频快速预筛（VAD 语音占比）· 真实标定（诚实版）

实现：`tools/asr/worker.py` 解码后、进 whisper 前，用 Silero VAD 算「语音占比」，
`< --vad-skip-ratio`（默认 0.12）直接记 `empty`，不进 whisper。零额外依赖（faster-whisper 自带）。

标定数据：`_run/vad_calib.json`（20 ok + 20 长音乐 + 部分短 suspect）+ `_run/vad_calib2.json`
（20 ok + 20 长音乐），脚本 `tools/asr/_run/vad_accuracy.py`。

| 阈值 | 长音乐/空（41 条）拦下 | 真语音 ok（40 条）误杀 | 短 suspect 语音（9 条）误杀 |
| --- | --- | --- | --- |
| 0.02 | 37 / 41（90%） | **1 / 40（2.5%）** | 0 / 9 |
| 0.05 | 37 / 41 | 4 / 40（10%） | 0 / 9 |
| **0.12（默认）** | **41 / 41** | **7 / 40（17.5%）** | 0 / 9 |
| 0.20 | 41 / 41 | 12 / 40（30%） | 0 / 9 |

- 分界：长音乐/空 的语音占比 0.0 ~ 0.083；短语音 0.59 ~ 1.0（干净）；
  但**长录音里「语音稀疏 + 大量 BGM」的 ok 文件也会低到 0.0037 ~ 0.10**，这是误杀来源。
- 上一版报告写「语音误杀 0/20」只用了短语音样本，**不完整**；本表已更正。
- **保守建议：以后重导入用 `--vad-skip-ratio 0.02`**（宁可漏判长音乐，也别误杀真语音）；
  默认 0.12 适合「只想快速扫一遍、可接受少量真语音被标 empty」的场景。
- 本次 pending=0 之后，预筛不会再被触发，除非将来重新导入/重扫。

## 5. 名片昵称修复

### 5.1 真实口径

- `media.kind='card'` 总计 **28,090** 条；其中 fallback 恰好为 `[名片]`（光秃）**12,367** 条（修复前）。
- 任务卡里的「211 条」是上一会话 14:57 的旧口径，已按 Lead 裁决作废；以 `data/qqscope.db` 实测为准。
- 光秃 `[名片]` 全部来自 `source='pack'`（NT 原始库解析），bot 源没有。

### 5.2 工具与口径

新工具：**`tools/asr/update_cards.py`**（只动「当前 `fallback=='[名片]'` 的 card 行」）：

1. 昵称来源优先级
   - ct=8 老布局：`nc_nickname_1`(47705) / `nc_nickname_2`(47714)；
   - ct=8 新布局 `media_sub=4`：pb2 未定义的字段 **48504/48505**（实测 48504==48505 共 1320/1323），
     判为名片主人；48506/48507/48508 是分享者信息（与 profile 库一致率只有 438/1216，不采用）；
   - 只有 uid：用 `core.sources.names.fetch_names()`（本地 profile/group 库，20,781 人）反查昵称；
     查不到就写 `[名片] uid:<uid>`。
2. 关闭项（本次不做，只计数）：`reply_f48271`（戳一戳/ark）、`f48214`（群成员提醒 gtip）、
   `ref_f47713`（撤回/引用摘要）、`filename`（文件）。
3. uid -> 昵称映射、UPDATE 都用事务批量提交；只改 `media` 的 `name`/`fallback` 两个键。

### 5.3 before -> after（真实数字）

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| `[名片]` 光秃 | **12,367** | **11,123** |
| `[名片] 昵称` / `[名片] uid:x` | 12,347 | 13,591 |
| 本轮补全 | — | **1,244** |

补全构成：老布局昵称 6 + 新布局 ms4 昵称 1,125 + uid 查名 87 + uid 原样 26 = 1,244。

未修（如实计数，见 §5.4）：戳一戳/ark 10,106、群提醒 321、撤回类 77、文件类 42、空壳 94、无 ct8 173。

### 5.4 修复后 5 条真实样例（id / 修复后 fallback / 原始字段）

| id | 修复后 | 来源字段 |
| --- | --- | --- |
| 533927 | `[名片] 羽莺1947III` | 老布局 `nc_nickname_1`=羽莺1947III |
| 534067 | `[名片] 希尔德` | 老布局 `nc_nickname_1`=希尔德 |
| 547309 | `[名片] 恋` | 新布局 ms4 `48504`=恋（48503=u_OsyvTxnt_cTPiftXo4EYoQ） |
| 547323 | `[名片] 柠檬Dawn` | 新布局 ms4 `48504`=柠檬Dawn |
| 547328 | `[名片] 常知安 INTJ 9w1 926 sp/so` | 新布局 ms4 `48504` |
| 44967 | `[名片] uid:u_7aRpfOwxXYuzOt2mEBd1EA` | 只有 uid、profile 库查不到 |

复现：`python tools/asr/update_cards.py --account 1605289411 --dry-run` -> 去掉 `--dry-run` 落库。

### 5.5 ct=8 元素细分（为什么还剩 11,123 条光秃 `[名片]`）

NT 原始库（c2c + group）ct=8 元素共 **25,749** 条，按载荷细分：

| 类别 | 判据 | 条数 | 正确 fallback 建议 |
| --- | --- | --- | --- |
| 真名片·有昵称 | `nc_nickname_1/2` 非空 | 4,118 | `[名片] 昵称` |
| 真名片·仅 uid | `nc_uid_1/2` 非空，且无下面 4 类标记 | 4,363 | `[名片] 昵称`（查库）或 `[名片] uid:x` |
| 撤回/引用类 | `nc_uid_1` 非空 + `ref_f47713` 非空 | 4,564 | `[撤回] <ref_f47713>` |
| 戳一戳/拍了拍（ark） | `reply_f48271` 非空 | 11,011 | `[戳一戳] <items[].txt>` |
| 群成员提醒（gtip） | 未知字段 `48214` 里含 `<gtip>` XML | 327 | `[群提醒] <nor txt>` |
| 文件类 | `filename`(45402) 非空 | 43 | `[文件: <filename>]` |
| 空壳 | 以上都没有 | 1,323 | 无法还原，保持原样 |

- 12,367 条光秃 `[名片]` 里，**10,106 条是戳一戳/ark、321 条是群提醒**（两者合计 84%），
  它们根本不是名片 —— 这是「名片看起来没昵称」的最大来源。
- **建议（本次未做，等 Lead 立项）**：在 `core/sources/_pack_extract.py::_classify(ct==8)` 增加
  `_nudge_label(d)`（解 `reply_f48271` 的 JSON，拼 `items[].txt`）和
  `_gtip_label(raw)`（扫 wire 字段 48214 的 gtip XML），仅在 `nick/uid` 都为空时生效。
  已实测：改动后 NT 侧 fallback 分布变为 `[名片] 14,411 / [戳一戳] 11,011 / [小程序] 1,322 /
  [合并转发] 565 / [群提醒] 327`；DB 侧光秃 `[名片]` 可从 11,123 降到约 1,000 量级。
  风险点：kind 仍是 `card`（不动 schema），只改 fallback 文案；会改变约 1 万条消息的显示文本，需跑
  `core/_export_selftest.py` 与 web 自检回归。

## 6. 「普通设备能用吗」——真实评估

### 6.1 本机实测

| 口径 | 音频 | 墙钟 | 有效倍速 |
| --- | --- | --- | --- |
| 20 条样本（16x2 + 预筛） | 531 s | 152~188 s | 2.8x ~ 3.5x |
| 收尾批次（10x2 + 预筛） | 1,172 s | 328 s | 3.57x |
| 混合负载（上一会话，音乐多、预筛收益大） | 1,719 s | 230 s | ~7.5x |

全量音频结构（29,901 s = 8.31 h）：ok 音频 11,626 s；empty 17,727 s（音乐/静音，预筛可快跳）；
**>120 s 的超长文件 62 条 = 20,555 s（占 69%）**。

外推：
- 保守（按 3.5x，假设全要过 whisper）：29,901 / 3.5 ≈ **8,540 s ≈ 2.4 h**。
- 乐观（预筛生效，真正进 whisper 的只有 ~12,100 s）：**约 1 ~ 1.5 h**。
- 截断 >120 s 到 90 s 后总音频 14,926 s（4.15 h），再省 ~50%。

### 6.2 按核数线性外推（仅供参考，现代笔记本单核更强、无 E5 内存带宽瓶颈）

| 设备 | 全量 8.31 h 音频 | 只转最近 50 条（0.60 h 音频） |
| --- | --- | --- |
| 本机 Xeon 20C/40T | 1 ~ 2.4 h | 10 ~ 16 min |
| 普通 8 核笔记本 | 2.5 ~ 6 h | 25 ~ 40 min |
| 普通 4 核笔记本 | 5 ~ 12 h | 20 ~ 30 min（按 4x2 线程估算） |
| 4 核 + 只转最近 50 条 | 不适用 | 20 ~ 30 min |

### 6.3 关键结论（给用户的说明）

> 转写是**一次性离线任务**。跑完后文字就存在 `data/qqscope.db` 的 `messages.media.voice_text` 里，
> **其他设备只读文本，完全不需要跑 ASR**，也不需要模型/显卡/Python 环境 —— 拷贝 `data/qqscope.db`
> 即可，文本随库走。本机也不用再跑了（pending=0）。

轻量用法（只转最近 N 条）：

```powershell
$env:PYTHONIOENCODING = "utf-8"
tools\\nt_msg_db_util\\.venv\\Scripts\\python.exe -m core.voice --account 1605289411 --recent 50 --workers 4 --threads 2 --yes
```

或 API：`POST /api/voice/transcribe {"account_qq":1605289411,"recent":50,"confirm":true}`

## 7. 本次改动清单

| 文件 | 改动 | 说明 |
| --- | --- | --- |
| `tools/asr/update_cards.py` | 新增 | 只补全 `fallback=='[名片]'` 的 card 行；支持老布局/新布局 ms4/uid 查名；`--dry-run` |
| `core/voice.py` | 小修 | `pending_jobs()` 不再用 `media.file IS NOT NULL` 过滤，缺文件的行才能被标记 `missing`（否则 pending 清不到 0） |
| `core/sources/_pack_extract.py` | 未改（已回滚） | 该文件在本轮开始前就已读 `nc_nickname_1`(47705)/`nc_nickname_2`；ct8 语义修正按 Lead 裁决另立项 |

报告与证据目录：`tools/asr/_run/`（`vad_calib*.json`、`vad_accuracy.py`、`final_evidence.py`、
`verify_fixed_samples.py`、`finish_run*.log`、`ab_test.log`、`ab_b.log`）。

## 8. 遗留 & 建议

1. **ct8 语义修正**（最大收益）：把 10106 条戳一戳/321 条群提醒从 `[名片]` 里区分出去，见 §5.5。
2. **预筛阈值**：默认 0.12 激进，重导入建议 0.02。
3. `suspect 7`（音乐上的重复幻觉）与 `decode 1`（id=48424）保持原状态，未强改成 ok。
4. `missing 1`（id=171786）本地文件确实不存在，无法本地转写；老消息可等 NapCat `fetch_ptt_text`
   （需登录态 + 7 天时效内）或从其他设备补文件后再转。
5. 语音条数仍在随 NapCat 实时同步增长（本次 664 -> 665），新语音会重新出现 pending；跑一次
   `python -m core.voice --account 1605289411 --workers 8 --threads 2 --yes` 即可清零。
"""
out.write_text(MD, encoding="utf-8", newline="\n")
print("wrote", out, len(MD), "chars")

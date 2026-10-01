# -*- coding: utf-8 -*-
from pathlib import Path
ROOT = Path(r"E:\01-项目\QQScope")
out = ROOT / "tools" / "asr" / "_run" / "VOICE_REPORT.md"
MD = """# QQScope · 本地语音转文字（ASR）· task-3 收尾报告（含 ct8 语义修正）

- 时间：2026-10-01（task-3，asr-fixer）
- 机器：Intel Xeon E5-2673 v4，20 核 / 40 线程，64 GB，纯 CPU（int8）
- 引擎：faster-whisper **small + int8**，worker = `tools/asr/worker.py`
- 账号：1605289411；主库：`data/qqscope.db`（全程事务写，未删/未覆盖）

## 0. 结论（TL;DR）

- **语音 pending = 0**。`GET /api/voice/stats?account=1605289411` = total 665 / **ok 542** / failed 123 / **pending 0** / lang_mismatch 15。
- **名片光秃 `[名片]`：12367 -> 833**。
  - 补全昵称/uid：1,244 条（`update_cards.py`）；
  - ct8 语义修正：10,290 条（`fix_ct8_labels.py`）-> `[戳一戳] 9970` + `[群提醒] 320`；
  - 剩余 833 = 617 条旧导入残留（无法从当前 NT 快照还原）+ 42 文件类 + 76 撤回类 + 94 空壳 + 4 无 ct8。
- **预筛阈值警告（见 §4）**：默认 `--vad-skip-ratio 0.12` 会误杀 **7/40（17.5%）** 真语音（长录音里语音稀疏）。
  **重新导入请用 0.02**（误杀 1/40，拦 37/41 长音乐）。
- 回归：`core/_export_selftest.py` **231/231**；`scripts/verify.py --full` **10/10，exit=0**。
- 后端 15555 **未重启**（voice_text 只写库；`core.voice.stats()` 与 API 逐字段一致）。

---

## 1. 语音最终进度（真实数字）

| 指标 | 数值 |
| --- | --- |
| voice_total | 665 |
| local_files | 664（1 条本地文件确实不存在） |
| **ok（有 voice_text）** | **542** |
| failed | 123 |
| **pending** | **0** |
| lang_mismatch | 15 |
| ok 音频 | 11,626 s（3.23 h） |
| 全部语音音频 | 29,901 s（8.31 h） |

status_counts：`{ok:542, empty:114, suspect:7, decode:1, missing:1}`

收尾批次（幂等续跑，ok/empty 自动跳过）：

| 批次 | 配置 | 结果 | 墙钟 | 音频 | 有效倍速 |
| --- | --- | --- | --- | --- | --- |
| 41 条（34 pending + 1 decode + 6 suspect） | 10 worker x 2 线程 | ok 19 / failed 22 | 328 s | 1,172 s | **3.57x** |
| 8 条 | 4 worker x 2 线程 | ok 0 / failed 8 | 215 s | 482 s | 2.24x |
| 1 条 missing | 2 worker x 2 线程 | missing 1 | 243 s | — | — |

### 1.1 单独列一条：`pending_jobs()` 过滤 bug（Lead 已采纳）

- **改前**：`core/voice.py::pending_jobs()` 的 WHERE 里带 `AND json_extract(media,'$.file') IS NOT NULL`。
- **后果**：`media.file` 为 null 的语音（本地文件缺失）**永远不会**进入任务队列 -> 永远不会被写
  `voice_status` -> `/api/voice/stats` 的 `pending` = `total - ok - failed` **永远清不到 0**（实测卡在 1）。
- **改后**：去掉该过滤，仍按 `media.kind='voice'` 取；这类行在 `_run_locked` 里走 `missing` 分支，
  如实写 `voice_status='missing'` + `voice_error='本地文件不存在'`。
- **效果**：id=171786（media.file=null）由 pending -> `missing`，随后 pending 归 0。

---

## 2. A/B（同一批 20 条，531 s 音频）

脚本 `tools/asr/ab_test.py` / `ab_b.py`，日志 `_run/ab_test.log` / `_run/ab_b.log`。

| 配置 | 墙钟 | 实时倍速 | ok/failed |
| --- | --- | --- | --- |
| A 旧：4 worker x 8 线程、带时间戳、无预筛 | 320.4 s | **1.66x** | 9 / 11 |
| B 新：16 worker x 2 线程、无时间戳、VAD 预筛 0.12 | 152.1 s | **3.49x** | 10 / 10 |
| B 新（看门狗放宽后复测） | 187.9 s | **2.83x** | 11 / 9 |
| 本次收尾混合负载（10x2） | 328 s | **3.57x** | — |

- 同批结论：**新配置快 1.7x ~ 2.1x**。
- 口径说明：`worker.py` 里 `beam_size=1`、`temperature=0`、`condition_on_previous_text=False`、
  `without_timestamps` 默认值**本来已内置**；A/B 真正对比的是
  「4x8 线程 + 带时间戳 + 无预筛」 vs 「16x2 线程 + 无时间戳 + VAD 预筛」。
- 混合负载观测到 2.24x ~ 7.5x（音乐/长静音占比越高，预筛收益越大）。

## 3. 线程超订与 worker 建议

- 旧 4x8 = 32 线程；10x8 = 80 线程挤 20 核 -> 明显超订，吞吐不涨。
- 16x2 = 32 线程吞吐上来了，但 16 个进程同时冷加载 small(int8) 会争内存带宽，2 s 小文件单条 wall
  涨到 34~85 s（看门狗曾误杀；已放宽到 `max(180, 10x时长, 上限600s)` + ready 心跳）。
- **建议稳态：`--workers 8~10 --threads 2`**（本次 10x2 实测 3.57x，且未拖垮 15555）。
- `tools/asr/_run/asr.lock` 互斥，禁止两套跑法叠加。

---

## 4. 长音频快速预筛（VAD 语音占比）· 真实标定（诚实版）

实现：`tools/asr/worker.py` 解码后、进 whisper 前，用 Silero VAD 算「语音占比」，
低于 `--vad-skip-ratio` 直接记 `empty`，不进 whisper。

数据：`_run/vad_calib.json` + `_run/vad_calib2.json`（40 ok + 41 长音乐/空 + 9 短 suspect），
脚本 `tools/asr/_run/vad_accuracy.py`。

| 阈值 | 长音乐/空（41）拦下 | 真语音 ok（40）误杀 | 短 suspect（9）误杀 |
| --- | --- | --- | --- |
| **0.02（建议重导入用）** | 37 / 41（90%） | **1 / 40（2.5%）** | 0 / 9 |
| 0.05 | 37 / 41 | 4 / 40（10%） | 0 / 9 |
| **0.12（当前默认）** | **41 / 41** | **7 / 40（17.5%）** | 0 / 9 |
| 0.20 | 41 / 41 | 12 / 40（30%） | 0 / 9 |

> **重要更正**：上一版报告写「语音误杀 0/20」，那是只用了 3~60 s 的短语音样本。
> 加上长录音样本后，真语音占比最低到 **0.0037**（id=252559，226 s，有文字），
> 与长音乐上限 0.0833 **有重叠区**。默认 0.12 会误杀 17.5% 的长语音，**重新导入务必降到 0.02**。

## 5. 名片修复（两阶段）

### 5.1 真实口径
`media.kind='card'` 共 **28,093** 条（随实时同步略有增长）；修复前 fallback 恰为 `[名片]`（光秃）**12,367** 条，
全部 `source='pack'`。任务卡「211 条」是上一会话 14:57 的旧口径，按 Lead 裁决作废。

### 5.2 阶段一：补昵称 / uid（`tools/asr/update_cards.py`）
只 UPDATE「当前 fallback=='[名片]'」的 card 行，昵称来源：
1. ct=8 老布局 `nc_nickname_1`(47705) / `nc_nickname_2`(47714)；
2. ct=8 新布局 `media_sub=4`：pb2 未定义字段 **48504/48505**（实测 48504==48505 共 1320/1323，
   与 profile 库一致率 1092/1213≈90%，判为名片主人）；48506/48507/48508 是分享者（一致率 438/1216，不采用）；
3. 只有 uid：`core.sources.names.fetch_names()`（20,781 人）反查；查不到写 `[名片] uid:<uid>`。

**结果：12,367 -> 11,123，补全 1,244**（老布局昵称 6 + 新布局 ms4 1,125 + uid 查名 87 + uid 原样 26）。

真实样例：

| id | 修复后 | 来源字段 |
| --- | --- | --- |
| 533927 | `[名片] 羽莺1947III` | 老布局 nc_nickname_1 |
| 534067 | `[名片] 希尔德` | 老布局 nc_nickname_1 |
| 547309 | `[名片] 恋` | 新布局 ms4 48504 |
| 547323 | `[名片] 柠檬Dawn` | 新布局 ms4 48504 |
| 547328 | `[名片] 常知安 INTJ 9w1 926 sp/so` | 新布局 ms4 48504 |
| 44967 | `[名片] uid:u_7aRpfOwxXYuzOt2mEBd1EA` | 只有 uid，profile 库查不到 |

### 5.3 阶段二：ct8 语义修正（`tools/asr/fix_ct8_labels.py`，Lead 已批准）

`kind` 仍是 `'card'`，不动 schema；仅当 `nc_nickname_*`/`nc_uid_*` 都为空时按判据给标签。

| 判据 | NT 字段号 | 渲染 | NT 命中 | DB 本次 UPDATE |
| --- | --- | --- | --- | --- |
| `reply_f48271` JSON 的 `items[].txt` | 48271 | `[戳一戳] <动作文案>` | 11,011 | **9,970** |
| 未知字段 48214 的 gtip XML `<nor txt="...">` | 48214 | `[群提醒] <文案>` | 327 | **320** |

- 执行三道账一致：`targets=10290` == `SELECT COUNT(*)=10290` == `UPDATE changed=10290`。
- **结果：光秃 `[名片]` 11,123 -> 833**；新增 `[戳一戳] 9,970` + `[群提醒] 320`。
- 备份：`data/backup/cards_ct8_20261001.jsonl`（10,290 行，每行 `{id, criterion, after_fallback, before_media}`）。
- **回滚步骤（一条命令）**：

```powershell
tools\\nt_msg_db_util\\.venv\\Scripts\\python.exe -c "import json,sqlite3;con=sqlite3.connect(r'E:\\01-项目\\QQScope\\data\\qqscope.db');[con.execute('UPDATE messages SET media=? WHERE id=?',(json.loads(l)['before_media'],json.loads(l)['id'])) for l in open(r'E:\\01-项目\\QQScope\\data\\backup\\cards_ct8_20261001.jsonl',encoding='utf-8') if l.strip()];con.commit();con.close()"
```
回滚后校验：`SELECT COUNT(*) ... json_extract(media,'$.fallback')='[名片]'` 应回到 **11,123**。

### 5.4 剩余 833 条光秃 `[名片]` 的最终分类账

| 类别 | 条数 | 说明 |
| --- | --- | --- |
| 未匹配到当前 NT 快照 | **617** | `content.type='reply'` 且字段全空，source=pack；旧导入/去重残留，当前 `nt_msg_clear.db` 里找不到对应行 |
| 文件类 | 42 | ct8 带 `filename`(45402)，不是名片 |
| 撤回/引用类 | 76 | ct8 有 uid + `ref_f47713`，语义更像撤回/引用 |
| 空壳 | 94 | ct8 无昵称/uid/ref/ark/filename/gtip，无信息可还原 |
| 无 ct8 段 | 4 | 该消息里根本没有 content_type=8 |
| **合计** | **833** | 已全部下账，无模糊项 |

### 5.5 ct8 元素细分（NT 库 25,749 条，供以后复查）

| 类别 | 判据 | 条数 |
| --- | --- | --- |
| 真名片·有昵称 | nc_nickname_1/2 | 4,118 |
| 真名片·仅 uid | nc_uid_* 且无下列标记 | 4,363 |
| 撤回/引用 | nc_uid + ref_f47713 | 4,564 |
| 戳一戳/ark | reply_f48271 | 11,011 |
| 群成员提醒 | 未知字段 48214 gtip | 327 |
| 文件类 | filename(45402) | 43 |
| 空壳 | 全空 | 1,323 |

---

## 6. 普通设备能用吗 —— 真实评估

### 6.1 本机实测
| 口径 | 音频 | 墙钟 | 有效倍速 |
| --- | --- | --- | --- |
| 20 条样本（16x2 + 预筛） | 531 s | 152~188 s | 2.8x ~ 3.5x |
| 收尾批次（10x2 + 预筛） | 1,172 s | 328 s | 3.57x |
| 混合负载（上一会话，音乐多） | 1,719 s | 230 s | ~7.5x |

全量结构（29,901 s = 8.31 h）：ok 11,626 s；empty 17,727 s（音乐/静音，预筛快跳）；
**>120 s 超长文件 62 条 = 20,555 s（69%）**。

外推：保守（3.5x）全量 **~2.4 h**；预筛生效（真正进 whisper ~12,100 s）**~1 ~ 1.5 h**；
把 >120 s 截断到 90 s 后总音频 14,926 s，再省 ~50%。

### 6.2 按核数外推（仅供参考；现代笔记本单核更强、无 E5 内存带宽瓶颈）

| 设备 | 全量 8.31 h 音频 | 只转最近 50 条（0.60 h） |
| --- | --- | --- |
| 本机 Xeon 20C/40T | 1 ~ 2.4 h | 10 ~ 16 min |
| 普通 8 核笔记本 | 2.5 ~ 6 h | 25 ~ 40 min |
| 普通 4 核笔记本 | 5 ~ 12 h | 20 ~ 30 min |

### 6.3 关键结论（给用户）
> 转写是**一次性离线任务**。文字存在 `data/qqscope.db` 的 `messages.media.voice_text` 里，
> **其他设备只读文本，完全不需要跑 ASR**（不需要模型/显卡/Python），拷贝 `data/qqscope.db` 即可。
> 本机已 pending=0，不用再跑。

轻量用法：
```powershell
tools\\nt_msg_db_util\\.venv\\Scripts\\python.exe -m core.voice --account 1605289411 --recent 50 --workers 4 --threads 2 --yes
```

## 7. 回归结果

| 检查 | 结果 |
| --- | --- |
| `core/_export_selftest.py` | **231/231 通过** |
| `scripts/verify.py --full` | **10/10 通过，exit=0** |
| `/api/media/stats` card | 修前 hit 0 / miss 28,090 -> 修后 hit 0 / miss 28,093（卡片本就没有本地文件，total 随库增长）|
| `GET /api/voice/stats` | pending 0；与 `core.voice.stats()` 一致 |

## 8. 改动清单

| 文件 | 改动 |
| --- | --- |
| `core/voice.py` | `pending_jobs()` 去掉 `media.file IS NOT NULL` 过滤（§1.1） |
| `core/sources/_pack_extract.py` | ct8 新增 `_nudge_label` / `_gtip_label`，仅 nick/uid 空时生效；`build_media` 传 `c.SerializeToString()` 作 raw |
| `tools/asr/update_cards.py` | 新增（昵称/uid 补全，`--dry-run`） |
| `tools/asr/fix_ct8_labels.py` | 新增（ct8 标签修正 + 备份 + 三数校验，`--dry-run`） |
| `tools/asr/_run/VOICE_REPORT.md`、`web/selftest/REPORT-asr.md` | 报告 |
| `data/backup/cards_ct8_20261001.jsonl` | 回滚快照（10,290 行） |

## 9. 遗留
1. 剩余 833 条光秃（§5.4）：617 条旧导入残留需重跑 pack 导入才能尝试还原；94 空壳/42 文件/76 撤回/4 无 ct8 属正常无昵称。
2. `suspect 7` / `decode 1` / `missing 1` 保持原状态，未强改。
3. 语音随 NapCat 实时同步增长，新语音会再出现 pending；跑一次续跑命令即可清零。
4. 老语音（>7 天）服务端 `fetch_ptt_text` 可能过期，仍需本地 ASR 兜底。
"""
out.write_text(MD, encoding="utf-8", newline="\n")
print("wrote", out, len(MD))

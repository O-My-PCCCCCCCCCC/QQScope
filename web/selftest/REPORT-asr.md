# task-3 语音/ASR + 名片修复 · 实测报告

> 交付物：本地 ASR 收尾（pending=0）+ 名片/ct8 修复 + 预筛标定 + 普通设备评估。
> 技术版：`tools/asr/_run/VOICE_REPORT.md`；原始证据：`tools/asr/_run/`、`data/backup/`。

## 1. 改动文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `core/voice.py` | 修 bug | `pending_jobs()` 去掉 `media.file IS NOT NULL` 过滤，缺文件行才能被标 `missing`（否则 pending 永远清不到 0） |
| `core/sources/_pack_extract.py` | 修 ct8 | 新增 `_nudge_label`(reply_f48271) / `_gtip_label`(未知字段 48214 gtip)，仅 `nc_nickname_*`/`nc_uid_*` 都为空时生效；kind 仍 `'card'`，不动 schema |
| `tools/asr/update_cards.py` | 新增 | 只补全 `fallback=='[名片]'` 的 card 行（老布局昵称 / 新布局 ms4 / uid 查名）；`--dry-run` |
| `tools/asr/fix_ct8_labels.py` | 新增 | ct8 标签修正 + 改库前快照 + 「预期/实际」三数校验；`--dry-run` |
| `data/backup/cards_ct8_20261001.jsonl` | 备份 | 10,290 行 `{id, criterion, after_fallback, before_media}`，回滚用 |
| `tools/asr/_run/VOICE_REPORT.md`、`web/selftest/REPORT-asr.md` | 报告 | 本文件与技术版 |

未触碰：`core/store.py`、`core/media.py`、`core/export.py`、`server/app.py`、`web/` 业务代码、`app/js/`、NapCat(node 39760)。
**未重启 15555**（voice_text 只写库；`core.voice.stats()` 与 API 逐字段一致）。

## 2. 验收 1：pending=0

`GET http://127.0.0.1:15555/api/voice/stats?account=1605289411`（httpx trust_env=False）：

```
voice_total 665 | local_files 664 | ok 542 | failed 123 | pending 0 | lang_mismatch 15
status_counts {ok:542, empty:114, suspect:7, decode:1, missing:1}
```
收尾 3 批（幂等）：10x2 线程 41 条（1172 s 音频 / 328 s = **3.57x**）、4x2 线程 8 条、2x2 线程补 1 条 missing。
`pending_jobs()` 过滤 bug 的改前/改后见 VOICE_REPORT §1.1（id=171786 由 pending -> missing）。**PASS**

## 3. 验收 2：A/B（同批 20 条，531 s）

| 配置 | 墙钟 | 实时倍速 | ok/failed |
| --- | --- | --- | --- |
| A 旧 4x8 线程 + 时间戳 + 无预筛 | 320.4 s | 1.66x | 9/11 |
| B 新 16x2 线程 + 无时间戳 + 预筛 | 152.1 s | 3.49x | 10/10 |
| B 复测（看门狗放宽） | 187.9 s | 2.83x | 11/9 |

同批 **1.7x ~ 2.1x**；混合负载（10x2）3.57x。**PASS**

## 4. 验收 3：预筛准确率（诚实标定）

`tools/asr/_run/vad_accuracy.py`（40 ok + 41 长音乐/空 + 9 短 suspect）：

| 阈值 | 长音乐拦下 | 真语音误杀 |
| --- | --- | --- |
| **0.02（建议）** | 37/41 (90%) | **1/40 (2.5%)** |
| 0.12（当前默认） | 41/41 | 7/40 (**17.5%**) |
| 0.20 | 41/41 | 12/40 (30%) |

长录音语音稀疏时占比低到 0.0037（与长音乐上限 0.0833 有重叠）；上一版「误杀 0/20」已更正。**PASS**

## 5. 验收 4：名片/ct8 修复

### 5.1 两阶段结果

| 阶段 | 命令 | 结果 |
| --- | --- | --- |
| ① 补昵称/uid | `python tools/asr/update_cards.py --account 1605289411` | 光秃 12,367 -> 11,123，补全 **1,244** |
| ② ct8 标签 | `python tools/asr/fix_ct8_labels.py --account 1605289411` | 光秃 11,123 -> **833**，改 10,290 条 |

阶段②三数一致：`targets=10290` == `SELECT COUNT(*)=10290` == `UPDATE changed=10290`。

### 5.2 判据 -> 命中数

| 判据 | 字段号 | 标签 | NT 命中 | DB 本次改 |
| --- | --- | --- | --- | --- |
| `reply_f48271` JSON `items[].txt` | 48271 | `[戳一戳] <文案>` | 11,011 | **9,970** |
| 未知字段 48214 gtip XML `<nor txt>` | 48214 | `[群提醒] <文案>` | 327 | **320** |

### 5.3 修复后 5 条真实样例

| id | 修复后 | 来源 |
| --- | --- | --- |
| 533927 | `[名片] 羽莺1947III` | 老布局 nc_nickname_1 |
| 547309 | `[名片] 恋` | 新布局 media_sub=4 字段 48504 |
| 547323 | `[名片] 柠檬Dawn` | 新布局 ms4 48504 |
| 547328 | `[名片] 常知安 INTJ 9w1 926 sp/so` | 新布局 ms4 48504 |
| 259084 | `[群提醒] 邀请加入了群聊，并附带了30条聊天记录。` | ct8 未知字段 48214 gtip |
| 259085 | `[戳一戳] 揉了揉 的头` | ct8 reply_f48271 |

### 5.4 最终剩余 833 条光秃账

| 类别 | 条数 |
| --- | --- |
| 未匹配到当前 NT 快照（旧导入残留，content.type=reply 全空） | 617 |
| 文件类（ct8 有 filename） | 42 |
| 撤回/引用类（uid + ref_f47713） | 76 |
| 空壳（ct8 无任何可辨识载荷） | 94 |
| 无 ct8 段 | 4 |
| **合计** | **833** |

### 5.5 回滚步骤（一条命令）

```powershell
tools\nt_msg_db_util\.venv\Scripts\python.exe -c "import json,sqlite3;con=sqlite3.connect(r'E:\01-项目\QQScope\data\qqscope.db');[con.execute('UPDATE messages SET media=? WHERE id=?',(json.loads(l)['before_media'],json.loads(l)['id'])) for l in open(r'E:\01-项目\QQScope\data\backup\cards_ct8_20261001.jsonl',encoding='utf-8') if l.strip()];con.commit();con.close()"
```
回滚后 `fallback=='[名片]'` 应回到 **11,123**。**PASS**

## 6. 验收 5：普通设备评估

| 设备 | 全量 8.31 h 音频 | 只转最近 50 条（0.60 h） |
| --- | --- | --- |
| 本机 Xeon 20C/40T | 1 ~ 2.4 h | 10 ~ 16 min |
| 普通 8 核笔记本 | 2.5 ~ 6 h | 25 ~ 40 min |
| 普通 4 核笔记本 | 5 ~ 12 h | 20 ~ 30 min |

> 转写是一次性离线任务；文字存在 `messages.media.voice_text`，**其他设备只读文本、完全不跑 ASR**，
> 拷贝 `data/qqscope.db` 即可。本机已 pending=0。

轻量用法：
```powershell
tools\nt_msg_db_util\.venv\Scripts\python.exe -m core.voice --account 1605289411 --recent 50 --workers 4 --threads 2 --yes
```
**PASS**

## 7. 回归

| 检查 | 结果 |
| --- | --- |
| `core/_export_selftest.py` | **231/231 通过** |
| `scripts/verify.py --full` | **10/10 通过，exit=0** |
| `/api/media/stats` card | 修前 hit 0 / miss 28,090 -> 修后 hit 0 / miss 28,093（卡片无本地文件，total 随库增长）|
| `GET /api/voice/stats` | pending 0；与 `core.voice.stats()` 一致 |

## 8. 遗留 / 未做
1. 剩余 833 条光秃中 617 条是旧导入残留，需重跑一次 pack 导入（`--reuse-clear`）才可能还原；建议单独立项。
2. `suspect 7` / `decode 1` / `missing 1` 保持原状态，未强改。
3. 语音随 NapCat 实时同步增长，新语音会再出现 pending；跑一次续跑命令即可清零。

# REPORT-v18 · 后端数据物理分账号（每账号独立库 + 迁移）

- 任务：task-11。用户要求「每个账号一个文件夹存放数据」。
- 结论：**Phase 1（存储层物理拆分）已完成并回归通过**；Phase 2（media_cache/avatars/export/voice/pack/decrypt 子目录的写入切换）本轮只落地了路径 helper，写入切换标为待办（见「未完成/风险」）。

## 一、已落地
1. `core/paths.py`
   - 新增 `ACCOUNTS_DIR=data/accounts`、`MASTER_DB=data/accounts.db`、`LEGACY_DB=data/qqscope.db`、`BACKUP_DIR=data/backup`。
   - 新增 `account_dir(qq)` / `account_db(qq)` / `account_media_cache|avatars|export|voice|pack|decrypt(qq)` / `accounts_split_done()` / `legacy_present()`。
2. `core/store.py`
   - `connect(account_qq=None)`：带参 -> `data/accounts/<qq>/qqscope.db`；不带 -> `data/accounts.db`（accounts 注册表）。
   - 迁移前兼容：`data/accounts/` 为空且旧库存在时，`connect(qq)` 回退旧单库**只读**（保证迁移前只读探测/verify/build 可用，绝不写旧库）。
   - 所有 per-account 函数内部改 `connect(account_qq)`（保留 `account_qq` 列）；`insert_messages/upsert_contacts/save_feeds` 按 `account_qq` 分组写各账号库。
   - `list_accounts()`：注册表 + 各账号库计数；`upsert_account` 同时写注册表与该账号库；`delete_account(qq)` 改为**删账号目录**（含库与文件）+ 注册表行。
   - `migrate_legacy_split()`：先 `sqlite3.backup` 备份到 `data/backup/legacy_split_<ts>.db`，按 account_qq 拆到各目录，旧库改名 `data/qqscope.db.migrated`（连同 -wal/-shm），**绝不删除**。
3. 调用点渗透：`core/live_sync/media/media_fetch/normalize/send/voice/voice_official/sources/{bot_source,pack_source,qzone}`、`server/app.py`、`server/routes_{media,profile}.py` 全部改为带账户连接；`qzone._connect(account_qq)`。
4. 迁移触发：`server/app.py` 启动事件调用 `store.migrate_legacy_split()`（可用 `QQSCOPE_AUTO_SPLIT=0` 关闭）；`scripts/migrate_split.py` 提供 CLI（默认 `--copy` 副本验证，`--yes` 才动真实库）。
5. 新增校验：`scripts/verify.py` 新增「每账号独立库布局」；`无孤儿账号行` 改为「每账号库内 account_qq 一致 + 注册表与目录一一对应」（迁移前回退旧库检查）。

## 二、迁移实测（只在副本上执行）
命令：`python scripts/migrate_split.py --copy`
结果（真实库副本）：
- 旧库拆出 **2 个账号目录**：
  - `1438830763`：contacts=4, messages=8, feeds=0（框架登录号，live 已入库）
  - `1605289411`：contacts=128, messages=283048, feeds=71（主号）
- 备份：`<临时根>/data/backup/legacy_split_20261001_223415.db`
- 旧库改名：`<临时根>/data/qqscope.db.migrated`
- 说明：feeds 表原本由 qzone 懒建，迁移时按旧库 DDL 在新账号库里补建后再拷贝（首轮发现 feeds 丢失，已修）。

## 三、回归结果
| 项目 | 结果 |
|---|---|
| `scripts/verify.py --full` | **13/13 exit=0**（含新增「每账号独立库布局」；迁移前显示「尚未迁移，重启自动拆分」） |
| `scripts/isolation_test.py` | **113/113 PASS**（真实双账号夹具已改为迁移到每账号库 + ATTACH/TEMP VIEW 真值；媒体跨账号用 B-only id 探测） |
| `check_v2..v15` | **302/0** 未回退 |
| `check_v16` | 22/0（断言随 task-11 更新为「delete_account 删目录+注册表行」） |
| `check_v17` | 32/0 |
| `check_v2..v17` 合计 | **356/0** |
| `scripts/migrate_split.py --copy` | 2 个账号目录，行数/备份路径如上 |

红线：真实 `data/qqscope.db` 全程只读（migrate 只在副本执行）；未重启 15555；未碰 NapCat。

## 四、迁移是否已在真实库执行 / 备份路径（回执要点）
- **未在真实库执行**：15555 正在录制且持有旧库句柄，Windows 下改名会失败/影响现场；按红线留给 Lead 统一重启。
- 重启 15555 时 `app` 启动事件会自动执行迁移：先备份到 **`E:\01-项目\QQScope\data\backup\legacy_split_<ts>.db`**，旧库改名为 `E:\01-项目\QQScope\data\qqscope.db.migrated`，再建 `data/accounts/<qq>/qqscope.db`。
- 也可手动预跑：`python scripts/migrate_split.py --yes`（同样先备份）。

## 五、未完成 / 风险（如实说明）
1. **Phase 2 未完成**：`media_cache/avatars/export/voice/pack/decrypt` 的写入路径仍走全局目录（`paths.account_*` helper 已提供，但 export/pack/decrypt/voice 的写盘点尚未切到账号子目录）。本轮优先保证「库级物理隔离 + 迁移 + 全量回归」，文件子目录切换下一轮做。
2. `data/avatars`、媒体解析源（Tencent Files）按 task-9 结论属公开/外部信息，是否按账号再分目录需产品确认。
3. 迁移是**一次性、幂等**的；若中途失败，旧库保持原样（备份在 data/backup/），不会丢数据。
---

## 六、Phase 2：文件级按账号落盘（2026-10-01 追加）

### 已切换的写盘路径
| 目录 | 新路径 | 切换点 |
|---|---|---|
| export | `data/accounts/<qq>/export/{<job>,_meta/<job>.json,<job>.zip}` | `core/export.py`（export/job_meta/zip）；`server/app.py` 的 `/api/export/list`、`/api/export/download` 按 account 目录 |
| media_cache | `data/accounts/<qq>/media_cache/` | `core/media_fetch.save_cache(account_qq, ...)`（唯一调用点已传 account） |
| avatars | `data/accounts/<owner>/avatars/<qq>.png`（无法判定归属则回退全局 `data/avatars`） | `server/routes_profile.py` 的 `/api/avatar`、`/api/avatar/group`；owner 由新增 `store.account_for_uin(uin)`（账号自身或唯一联系人）判定 |
| decrypt | `data/accounts/<qq>/decrypt/` | `core/sources/pack_source.py::_decrypt_dir`、`core/sources/names.py`（2 处 work 目录） |
| pack | `data/accounts/<qq>/pack/`（helper 已就绪） | 本轮未切：`server/onebot.py`、`server/reader.py` 不在本任务允许改动范围；`data/pack` 现仅剩 2 个 root 级文件 |
| voice | `data/accounts/<qq>/voice/` | 真实 `data/voice` 不存在，当前无写盘点 |

### 一次性迁移脚本
`scripts/migrate_files_split.py`：
- `--selftest`（默认安全）：合成小夹具自测 -> **7/7 PASS**（media 唯一 md5 归位、无主 media 留原处、avatar 归属、无主 avatar 留原处、export 按 _meta 归位、decrypt/<qq> 归位、迁移前备份）。
- `--dry`：只读枚举，不移动、不备份。
- `--yes`：真实执行；执行前把 export/media_cache/avatars/decrypt/pack/voice 整体备份到 `data/backup/files_split_<ts>/`。
- 归属规则：export 按 `_meta/<job>.json.account_qq`；media_cache 按各账号库 `messages.media.md5` 唯一命中；avatars 按 `store.account_for_uin`；decrypt/pack 按目录名 = 账号 QQ。
- **无法判定的文件不删、不移**，原处保留并计入 `left` 清单。

### 真实库 --dry 结果（未移动任何文件）
- 可判定归属（将移动）：export 30、media_cache 1551、avatars 127、decrypt 3、pack 2、voice 0。
- 无法判定（留在原处）：export 52（无 `_meta` 的旧 job）、media_cache 28（无 md5 命中/多账号同名）、avatars 212（联系人未唯一映射到某个账号）、decrypt 14（非账号目录，如 profile/test_*/xiaohao）、pack 2（root 级 `messages.json.gz`/`meta.json`）、voice 0。

### 迁移是否已在真实库执行
**未执行**。15555 当前仍运行 Phase 1 代码、读全局目录；此时移动文件会让在线服务读不到（头像/导出/媒体缓存会短时失效）。请 Lead 重启 15555 到 Phase 2 代码后执行：
`python scripts/migrate_files_split.py --yes`（备份 `E:\01-项目\QQScope\data\backup\files_split_<ts>\`）。

### Phase 2 回归
| 项目 | 结果 |
|---|---|
| `scripts/migrate_files_split.py --selftest` | 7/7 PASS |
| `scripts/verify.py --full` | **13/13**（导出实测产物已落在 `data/accounts/1438830763/export/exp_...`） |
| `scripts/isolation_test.py` | **113/113** |
| `check_v2..v17` | **356/0**（v15 一条 export _meta 断言随账号目录更新） |

红线：真实 data/qqscope.db 未改；未重启 15555；未碰 NapCat；文件迁移在真实库仅做 `--dry` 枚举。
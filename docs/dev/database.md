# 数据库

> 提交: `7bd4f6c`
>
> Alembic 标准命令直接查 [官方文档](https://alembic.sqlalchemy.org/). 本文只记 Amane 特有的决策与陷阱.
> 数据模型设计见 [data-model.md](data-model.md).

## 三个数据库, 各管各的

主库不是项目里唯一的数据库. 边界清晰:

| 库 | 引擎 | 归属 | 进 Alembic? |
|----|------|------|------------|
| `amane.db` | SQLite | 主业务库 (metadata/tasks/resources...) | ✅ 启动期 `upgrade head` |
| `translations.db` | SQLite | LLM 译文缓存 (会话级, 删文件即清空) | ❌ 自建表, 见 [llm.md](llm.md) |
| r18.dev 镜像 | **PostgreSQL** | 外部只读数据源 (用户自备实例) | ❌ schema 由 r18 dump 决定, 项目不拥有 |

r18 库由项目导入/管理但 schema 不受我们控制 (见 [crawlers.md](crawlers.md) "r18.dev 离线 PG 镜像").
它**绝不能进 Alembic** — 我们对它只读, schema 随 r18 的 dump 走; 兼容性靠导入期探针校验保证, 不靠迁移.

## SQLite 选型

选择 SQLite 而非 PostgreSQL/MySQL 的原因:

- 目标用户是个人媒体库, 单机部署, 无需多进程并发写入.
- 零运维: Docker 用户无需额外数据库容器.
- 文件级备份方便 (见下方迁移前自动备份; 手工备份须用 online backup, **不要**在 WAL 模式下只 `cp amane.db`).

已知约束: 不支持部分 ALTER TABLE; 写入排他锁; JSON 字段无法高效查询. 个人规模可接受, 超过百万行需评估迁移到 PostgreSQL.

## 启动期自动迁移与安全网

`create_async_engine_from_path` (`src/amane/db/engine.py`) 在创建业务引擎**之前**调用 `upgrade_sqlite_database` (`src/amane/db/sqlite_migrate.py`):

1. **若当前 revision 已是 head**: 什么都不做 (不备份、不升级).
2. **若落后 head**: 先用 sqlite3 **online backup API** 写
   `{db}.pre-migrate-{oldrev}-{utc}.bak` (含 WAL 一致快照; 同目录保留最近 5 份), 再升级.
3. **迁移连接**单独 `autocommit=False` + Alembic `transactional_ddl=True`: 单次 revision 内 DDL 与 `alembic_version` 同事务; 失败则回滚, 避免「表已创建但版本未升」的半成品. 业务连接不改事务模式, 仍只开 WAL + FK.

CLI (`uv run alembic …`) 走同一套 `env.py` 事务性 DDL; CLI **不会**自动备份 — 依赖启动路径或手工 backup. `env.py` 自行创建的引擎在 upgrade 结束后必须 `dispose()`, 否则同一进程内多次升级 (测试) 会留下未关闭的 sqlite3 连接.

SQLite 不能直接存储 Python `datetime`, 须先转为字符串. SQLAlchemy 管理的 DateTime 列会自行转换; 迁移回填中的裸 `INSERT` 不会, 会触发 Python 3.12 已弃用的默认转换. `sqlite_migrate` 在导入时注册相同格式 (`2026-01-02 03:04:05`) 的转换, 使裸 SQL 绑定 datetime 仍然有效.

用户更新版本后**不需要手动迁移** — Docker 拉新镜像重启即可. 启动失败时:

- `AMANE_DATA_DIR` 指向独立目录隔离
- 用最近的 `*.pre-migrate-*.bak` 恢复 (先停服务, 换回主库文件并清掉 `-wal`/`-shm`)
- 或 `uv run alembic downgrade <rev>` (仅 schema 可逆时)

## Batch Mode

SQLite 不支持删列、改类型等 ALTER TABLE 操作. Alembic 通过 batch mode 重建整表来绕过:

- 创建临时表 (新 schema) → 拷贝数据 → 删旧表 → 重命名
- 代价: 大表改动会卡 (全量拷贝)
- 当前规模可接受
- 整次 revision 仍包在上述事务性 DDL 事务里 (失败应整体回滚)

## Autogenerate 盲区

`alembic revision --autogenerate` 不能检测:

- **列重命名** — 看作 "删旧列 + 加新列", 数据丢失. 必须手写 batch op.
- **索引/约束改动** — 部分漏检. 生成完一律审一遍. SQLite 在 SQLAlchemy 2 下无法反射表达式索引, autogenerate 会 skip (如 `ix_feed_items_list_order`), 须手补 `CREATE INDEX`.
- **已知噪声**: `tasks.type` / `schedules.task_type` 每次 autogenerate 都报 VARCHAR→Enum 的 `modify_type` diff. 这是 SQLite 无原生 enum + SQLModel 反射差异导致的假阳性, 与实际 schema 无关, 忽略即可.
- **JSON 列内部结构** — `Metadata.raw` 等 blob 不在 autogenerate 视野里. 爬虫/聚合模型改字段名或类型时, **结果列与 raw 快照是两份数据**; 只改列定义不够, 必须另写 data revision 扫 JSON (见 `c4f17334c3ea`). 站点级复用会把 raw 直接 `MediaMetadata(...)`, 旧 key 会被 Pydantic 静默丢掉.
- **JSON 列表列** — `Library.patterns` / `copy_resources` 等非空, 空集合存 `[]` 不是 JSON `null`. 改为 NOT NULL 须先 `UPDATE … '[]'` 回填 (含字面量 `'null'`), 再 `batch_alter_table` `nullable=False`.
- **path 投影列** — `MediaFile.content_type` / `mosaic` 与 `status` 同为 SA Enum (持久化成员名, 回填 `.name`); `definition` 不是枚举. 丢掉 `ix_metadata_number` / `schedules.task_type` 噪声.

## 加非空 FK + 数据回填 (一次性原子迁移)

给**已有数据**的表加**非空** FK 列不能一步到位, 否则旧行违反约束. 因启动期自动 `upgrade head`, 全部步骤必须在一个 `upgrade()` 内原子完成:

1. `batch_alter_table` 加**可空**新列 (SQLite 重建表)
2. 用 `op.get_bind()` 跑裸 SQL **回填** (basename、最长前缀匹配归属等)
3. 匹配不到的行落入一个**兜底行** (如不可删的 `Unmanaged` library), 保证迁移不中止
4. 再 `batch_alter_table` 把回填好的列 `alter_column(nullable=False)` + `create_foreign_key` + `create_index`

迁移测试用临时文件 DB: `command.upgrade(cfg, "<prev>")` → 插旧 schema 数据 → `upgrade("head")` → 断言回填结果 (见下方测试策略).

## 测试策略

| 场景 | 方式 | 原因 |
|------|------|------|
| 业务逻辑测试 | 文件 DB + `copy_schema()` (schema_template.py) | 快 (进程级模板, 避免重复跑 24 个 Alembic revision), 不依赖迁移历史 |
| 迁移逻辑测试 | 临时文件 DB + `command.upgrade` | 测试 "旧→新" 路径 |
| 备份 / 事务性 DDL | `tests/db/test_sqlite_migrate_safety.py` | WAL 一致备份、失败 revision 回滚、启动路径冒烟 |

schema_template.py 在进程首次调用时构建已迁移的 SQLite 模板并 vacuum, 后续测试直接拷贝文件, 避免为每个测试重复跑 Alembic. 例外: 测迁移本身 (`test_engine` / `test_sqlite_migrate_safety`) 必须从空文件起步.

不在业务测试中跑迁移 — `copy_schema()` (从预构建的进程级 schema 模板拷贝) 绕过迁移, 保证测试的是当前 schema 而非迁移路径.

## 路径与配置

`alembic.ini` 中 `sqlalchemy.url` 默认 `sqlite:///data/amane.db`, 与 `AMANE_DATA_DIR=./data` 对齐. 改了 `data_dir` 后手动跑 alembic 命令需:

```bash
AMANE_DATA_DIR=/your/path uv run alembic ...
```

`env.py` 读取环境变量覆盖 URL.

## 迁移工作流

1. 修改 `src/amane/db/models.py`.
2. `uv run alembic revision --autogenerate -m "描述"` (数据迁移用 `revision` 不加 `--autogenerate`).
3. **绝对禁止手写 revision ID** — 必须由 `alembic revision` 生成, 保证全局唯一.
4. 审 `src/amane/db/migrations/versions/` 下生成的脚本 — 重命名/特殊改动手补.
5. `uv run alembic upgrade head` (本地验证) 或重启服务 (自动跑; 落后时会先写 pre-migrate 备份).
6. 跑测试 + 提交迁移文件.

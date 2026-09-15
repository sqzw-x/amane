# 数据库

> Alembic 标准命令直接查 [官方文档](https://alembic.sqlalchemy.org/). 本文只记 Amane 特有的决策与陷阱.
> 数据模型设计见 [data-model.md](data-model.md).

## 数据库边界

主库不是项目里唯一的数据库:

| 库 | 引擎 | 归属 | 纳入 Alembic |
|----|------|------|------------|
| `amane.db` | SQLite | 主业务库 (metadata / tasks / resources ...) | ✅ 启动期 `upgrade head` |
| `translations.db` | SQLite | LLM 译文缓存 (会话级, 删文件即清空) | ❌ 自建表, 见 [llm.md](llm.md) |
| r18.dev 镜像 | **PostgreSQL** | 外部只读数据源 (用户自备实例) | ❌ schema 由 r18 dump 决定, 项目不拥有 |

r18 库由项目导入 / 管理但 schema 不受我们控制, **不能纳入 Alembic** — 项目对其只读; 兼容性靠导入期探针校验, 不靠迁移, 见 [crawlers.md](crawlers.md).

## SQLite 选型

目标用户是个人媒体库单机部署: 无需多进程并发写入, 零运维 (Docker 用户不必额外起数据库容器), 文件级备份方便. 已知约束是不支持部分 ALTER TABLE、写入排他锁、JSON 字段无法高效查询 — 个人规模可接受, 超过百万行需评估迁移到 PostgreSQL. 手工备份必须用 online backup, WAL 模式下不能只 `cp amane.db`.

## 启动期自动迁移与安全网

`create_async_engine_from_path` (`db/engine.py`) 在创建业务引擎**之前**调用 `upgrade_sqlite_database` (`db/sqlite_migrate.py`):

1. **当前 revision 已是 head**: 什么都不做 (不备份、不升级).
2. **落后 head**: 先用 sqlite3 **online backup API** 写 `{db}.pre-migrate-{oldrev}-{utc}.bak` (含 WAL 一致快照; 同目录保留最近 5 份), 再升级.
3. **迁移连接**单独 `autocommit=False` + Alembic `transactional_ddl=True`: 单次 revision 内 DDL 与 `alembic_version` 同事务, 失败则回滚, 避免「表已创建但版本未升」的半成品. 业务连接不修改事务模式, 仍只开 WAL + FK.

CLI (`uv run alembic …`) 使用同一套 `env.py` 事务性 DDL, 但**不会**自动备份 — 依赖启动路径或手工 backup. `env.py` 自行创建的引擎在 upgrade 结束后必须 `dispose()`, 否则同一进程内多次升级 (测试) 会留下未关闭的连接.

SQLite 不能直接存储 Python `datetime`, 迁移回填中的裸 `INSERT` 须依赖 `sqlite_migrate` 在导入时注册的字符串转换, 否则会触发 Python 3.12 已弃用的默认转换.

用户更新版本后**不需要手动迁移**; 启动失败时可用 `AMANE_DATA_DIR` 指向独立目录隔离, 或用最近的 `*.pre-migrate-*.bak` 恢复 (先停服务, 换回主库文件并删除 `-wal` / `-shm`), 或 `uv run alembic downgrade <rev>` (仅 schema 可逆时).

## Batch Mode

SQLite 不支持删列、改类型等 ALTER TABLE 操作, Alembic 通过 batch mode 重建整表绕过 (创建临时表 → 拷贝数据 → 删旧表 → 重命名). 代价是大表改动会卡 (全量拷贝), 当前规模可接受; 整次 revision 仍包在事务性 DDL 事务里, 失败应整体回滚.

## StrEnum 列

枚举型表列用 `Field(Enum)`: SQLAlchemy 按成员名落库, ORM `select` 得到真正的枚举, FastAPI / Pydantic 对外仍是 value. Python 里成员本身就是字符串, 不能写多余的 `.value`; 注解要 `str` 时用 `str(e)`.

| 层 | 形态 |
|----|------|
| Python / API / OpenAPI / 配置 TOML | value |
| JSON 列、`Task.payload`、`translations.db` | value |
| 枚举型表列 (`Field(Enum)`) | **成员名** |

SQLite 没有原生 enum, 列仍是 VARCHAR. 不能使用 `Column(String)` 或 `values_callable` 按 value 落库 — ORM 会读出 `str`. 裸 SQL 绑定成员名; ORM 比较用 `is`.

## Autogenerate 盲区

`alembic revision --autogenerate` 不能检测:

- **列重命名** — 看作「删旧列 + 加新列」而丢数据, 必须手写 batch op.
- **索引 / 约束改动** — 部分漏检, 生成完一律审一遍. SQLite 在 SQLAlchemy 2 下无法反射表达式索引, autogenerate 会 skip, 须手补 `CREATE INDEX`.
- **已知噪声**: SQLite 无原生 enum, 反射把 SA Enum 列看成 VARCHAR, autogenerate 会报 `modify_type`; 与实际 schema 无关, 忽略.
- **JSON 列内部结构** — `Metadata.raw` 等 blob 不在 autogenerate 视野里. 爬虫 / 聚合模型修改字段名或类型时**结果列与 raw 快照是两份数据**, 只修改列定义不够, 必须另写 data revision 遍历 JSON; 站点级复用会把 raw 直接交给 `MediaMetadata`, 旧 key 会被 Pydantic 静默丢掉.
- **JSON 列表列** — 非空列的空集合存 `[]` 不是 JSON `null`, 改为 NOT NULL 须先 `UPDATE … '[]'` 回填 (含字面量 `'null'`), 再 `batch_alter_table` `nullable=False`.
- **path 投影列** — `MediaFile.content_type` / `mosaic` 与 `status` 同为枚举列, `definition` 不是枚举.

## 非空 FK 与数据回填

给**已有数据**的表加**非空** FK 列不能一步到位, 且因启动期自动 `upgrade head`, 全部步骤必须在一个 `upgrade()` 内原子完成:

1. `batch_alter_table` 加**可空**新列 (SQLite 重建表).
2. 用 `op.get_bind()` 执行裸 SQL **回填**.
3. 匹配不到的行落入一个**兜底行** (如不可删的 `Unmanaged` library), 保证迁移不中止.
4. 再 `batch_alter_table` 把回填好的列 `alter_column(nullable=False)` + `create_foreign_key` + `create_index`.

迁移测试用临时文件 DB: `command.upgrade(cfg, "<prev>")` → 插旧 schema 数据 → `upgrade("head")` → 断言回填结果.

## 测试策略

| 场景 | 方式 | 原因 |
|------|------|------|
| 业务逻辑测试 | 文件 DB + `copy_schema()` (`schema_template.py`) | 快 (进程级模板, 避免重复跑全部 Alembic revision), 不依赖迁移历史 |
| 迁移逻辑测试 | 临时文件 DB + `command.upgrade` | 测试「旧→新」路径 |
| 备份 / 事务性 DDL | `tests/db/test_sqlite_migrate_safety.py` | WAL 一致备份、失败 revision 回滚、启动路径冒烟 |

`schema_template.py` 在进程首次调用时构建已迁移的 SQLite 模板并 vacuum, 后续 `copy_schema()` 拷贝该文件, 因此测的是当前 schema 而非迁移路径; 用法与例外见 [testing.md](testing.md).

## 路径与配置

`alembic.ini` 只提供 `script_location`; CLI 迁移所需的库路径由 `env.py` 按 `AMANE_DATA_DIR` (默认 `./data`) 计算, 该目录不存在时先创建. 应用启动不读 ini, 脚本目录与 URL 由 `sqlite_migrate.upgrade_sqlite_database` 直接给出.

## 迁移工作流

1. 修改 `src/amane/db/models.py`.
2. `uv run alembic revision --autogenerate -m "描述"` (数据迁移用 `revision` 不加 `--autogenerate`), **绝对禁止手写 revision ID**.
3. 审生成的脚本 — 重命名 / 特殊改动手补, 并补翻译之外的 data revision.
4. `uv run alembic upgrade head` 本地验证 (或重启服务自动跑), 然后运行测试并提交迁移文件.

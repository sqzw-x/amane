# 字符串枚举

> 提交: `HEAD`
>
> Python 一律 `StrEnum`. 成员本身就是对外字符串.
> 字段清单、成员名去源码.

## 成员即 value

`ContentType.CENSORED == "censored"` 为真; `str()` / f-string / JSON (`model_dump(mode="json")`) / dict 键 / `in` 集合都是 **value**. 直接写枚举成员, 与写那串字符等价.

静态类型里 `StrEnum` 不是 `str` (`dict` 键 invariant). 注解要求 `str` 时写 `str(e)`, 不要 `.value`; 运行时仍是 value.

`.name` (`CENSORED`) 只在枚举型表列的磁盘形态出现. ORM `select` 已经是成员, 比较用 `is` / `==`.

`.value` 仍出现的地方: `dict.values()`、表单 `currentTarget.value`、pytest `exc.value`、字段就叫 `value` 的结构 (插件加载失败的 `path=failure.value`).

## 三层各用各的形态

| 层 | 形态 | 例子 |
|----|------|------|
| Python / API / OpenAPI / 配置 TOML | value | `"censored"`, `"scrape"`, `"javdb"` |
| JSON 列、`Task.payload`、`translations.db` | value | `copy_resources`, `Feed.use_cache` |
| **枚举型表列** (SQLAlchemy Enum) | **成员名** | `CENSORED`, `SCRAPE` |

SQLite 没有原生 enum, 列仍是 VARCHAR. SQLAlchemy 按成员名查找; 读出来是真正的枚举 (`is ContentType.CENSORED`). FastAPI/Pydantic 对外序列化 value, 没有额外 ser/de.

## 表列

与 `MediaFile.status` 相同: `Field(Enum)` / `Enum | None = None`. 定义见 `src/amane/db/models.py`.

用 `Column(String)` 存 value 或 `values_callable` 按 value 落库, `select` 会得到 `str` 而不是成员, 调用方就会出现 `ContentType(x)` / `isinstance` 分支.

存量 value → 名: 迁移 `UPDATE` (`member.value` → `member.name`); 可空列上的非法值置 NULL. 裸 SQL 断言看名字 (`"CENSORED"`), ORM 断言用 `is`. 新行由 ORM 绑定名字.

`alembic revision --autogenerate` 会把 SQLite VARCHAR 和 SA Enum 报成 `modify_type` 假阳性 (`tasks.type` / `schedules.task_type` 以及其它枚举列). 与实际 schema 无关, 忽略.

迁移与启动备份见 [database.md](database.md).

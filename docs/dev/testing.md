# 测试

> 运行方式见 `just test`. 爬虫 TOML 见 [crawler-testing.md](crawler-testing.md). 夹具路径见下; 测试范围与禁止形态见文末.

优先使用现成 fixture, 不能自建引擎: `repo` / `resource_store` (`tests/conftest.py`) 已拷 head schema; HTTP 经由 `client` 或 `make_app` (`tests/api/conftest.py`), 后者会 `copy_schema` 并把 `worker.poll_interval` 压到配置下限.

必须自开文件库时调用 `copy_schema` (`tests/schema_template.py`), 不能在每个测试里 `create_all` / Alembic. **例外**: 测迁移本身必须从空文件起步 (`test_engine` / `test_sqlite_migrate_safety`).

GET `/config` 全量相等用 `hot_for_tests()`, 不能与裸 `HotSettings()` 比较 (夹具 poll 不是生产默认).

真文件系统的 watcher 集成: 把 `observer_timeout` / `check_interval` 收到 0.1 / 0.05 (默认 1s); 否定断言用短 `wait_for(duration=…)`, 不能使用秒级 sleep.

## 测试分层

语义在哪一层实现, 就在哪一层测. HTTP 集成必须存在, 但不能用 `client` 覆盖 repo / 纯函数已经覆盖的表.

| 语义 | 测试位置 | HTTP 只留 |
|------|--------|-----------|
| 分类 rename/merge/delete/规则 | `tests/db/test_facets.py` | `tests/api/test_facets_write.py` 状态码与 JSON |
| 演员字段筛选 / `ActorBrowseParams` 校验 | `tests/db/test_actor_browse.py` | `tests/api/test_actors.py` 列表剥字段、详情/PATCH/刮削 |
| metadata/media/task 列表筛选、级联删除、任务链 | `tests/db/test_repository.py` / `test_task_links.py` | 空列表、422、响应里的 DTO 字段 (如 `file_phase` / `child_count`) |
| 来源 merge (`compute_merge_updates`) | `tests/aggregate/test_merge_updates.py` | 400/404/422 与一次写库 |
| 影片 `FilmActor` / 性别填空 / 建演员写入 | `tests/crawlers/test_film_actor.py` · `test_javdb_actors.py` · `tests/aggregate/test_engine.py` · `tests/db/test_actor_gender_seed.py` | — |
| 任务 batch 计数/跳过链/取消 fallback | `tests/db/test_task_batch.py` (`execute_task_batch`, 无 lifespan) | `POST /tasks/batch` 接线 |
| feed item 搜索/状态/分组 | `tests/db/test_feeds.py` | CRUD、poll、按 feed 配置入队刮削 |

`client` 每次进入都付一次 FastAPI lifespan. 同一资源的 CRUD / 校验 / 空列表放入**同一个**测试函数, 用循环执行表测试, 不能用 `@pytest.mark.parametrize` 乘 `client`. 建库默认会入队 REFRESH, 不测扫描时显式 `scan=False`. 必须独占 worker 的 (claim、复用活跃任务) 才用 `stop_worker`. 解析 / 爬虫 / 纯函数测试不经由 lifespan, 不必为墙钟去合并.

CI: Ubuntu 执行完整 `just ci` (含 generate / 前端 / coverage). Windows 执行 `just ci-windows` (pytest, 无 Node). 盘符、原生分隔符、跨盘 `commonpath` 并不都标 `skipif`, 因此 Windows 仍执行全套 Python 测试, 而非仅 win32 用例; 前端与类型检查与 OS 无关, 无需在 Windows 安装 pnpm/Node.

## 玩具测试

覆盖率是副产品, 不是目标. 测行为与契约, 不能测「源码里已经写着的那个值」或「把实现再抄一遍」. 下列形态不能加入:

| 形态 | 判别 |
|------|------|
| 默认值回声 | 构造 `Model()` / `Config()` 后逐字段断言等于源码里的 `Field(default=…)`. 默认值的真源是模型定义与 OpenAPI, 不是测试. |
| 实现复述 | 测试体与被测函数同一套表达式 (`assert get_version() == pkg_version(PACKAGE_NAME)` 而函数就是这一行; 或用源码同一条件当期望值去扫描注册表). 把 SUT 换成它自己的拷贝, 测试仍会过, 就是复述. |
| 透传 / mock 回声 | 一层 `return await inner.foo(...)` 的包装: mock 返回 X 再断言 X. 测的是 mock, 不是逻辑. |
| 只断言存在 | `assert obj is not None` / `assert limiter is not None` 且不再看属性或行为. `None` 作为有意义的分支 (缺密钥不装配) 才算. |
| 常量快照 | `assert MODULE.CONST == {手抄一份}`. 两边一起改仍绿. |
| 写入再回读 | ORM/Pydantic 填字段、commit、断言还是那些字段. SQLAlchemy 不会把刚写入的值偷偷改掉. |

必须覆盖: 校验拒绝与报错信息; 旧字段/迁移; DB 真正强制的唯一/检查约束; 解析/分类的边界与非法输入; 加枚举成员时必须补齐的 frozen-dict / 路由资格; 对外冻结的表面 (SDK 按 identity 再导出、capability 工具名集合). 路径拼接若只是 `data_dir / "config.toml"` 一句话, 也不能单开测试.

表测试优先; 合法路径之外必须有非法/空输入. 发现玩具测试直接删除, 不能改成「断言得更细的同一回声」.

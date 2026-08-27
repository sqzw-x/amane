# 加测试

> 提交: `bf227f36`
>
> 怎么跑见 `just test`. 爬虫 TOML 见 [crawler-testing.md](crawler-testing.md). 本文只写夹具该走哪条路.

能用现成 fixture 就不要自建引擎: `repo` / `resource_store` (`tests/conftest.py`) 已拷 head schema; HTTP 走 `client` 或 `make_app` (`tests/api/conftest.py`), 后者会 `copy_schema` 并把 `worker.poll_interval` 压到配置下限.

必须自己开文件库时调 `copy_schema` (`tests/schema_template.py`), 不要每测 `create_all` / Alembic. **例外**: 测迁移本身必须从空文件起步 (`test_engine` / `test_sqlite_migrate_safety`).

GET `/config` 全量相等用 `hot_for_tests()`, 不要和裸 `HotSettings()` 比 (夹具 poll 不是生产默认).

真文件系统的 watcher 集成: 把 `observer_timeout` / `check_interval` 收到 0.1 / 0.05 (默认 1s); 否定断言用短 `wait_for(duration=…)`, 不要秒级 sleep.

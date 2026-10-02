"""从任务记录目录/zip 执行 ScrapeHandler (offline 优先)."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..app.runtime import build_network_stack, build_r18_db
from ..config import HotSettings
from ..crawlers.factory import CrawlerFactory
from ..crawlers.http import HttpClient
from ..crawlers.r18dev import R18Database
from ..db.engine import create_async_engine_from_path
from ..db.repository import Repository
from ..handlers import ScrapeHandler
from ..handlers.models import CacheKind, ScrapePayload
from ..media import ResourceStore
from ..net.http import WebClient
from .models import RecordManifest, TaskSnapshot
from .replay import ReplayWebClient

if TYPE_CHECKING:
    from ..net.browser import BrowserPool


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="amane.observability", description="Replay a scrape task from a task record")
    parser.add_argument("record", type=Path, help="task record directory or zip")
    parser.add_argument("--online", action="store_true", help="force live HTTP even if http/ dump is present")
    args = parser.parse_args(argv)
    return asyncio.run(run_record(args.record, online=args.online))


async def run_record(record_path: Path, *, online: bool = False) -> int:
    with _open_record(record_path) as root:
        manifest = RecordManifest.model_validate_json((root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.record_version not in (1, 2):
            print(f"unsupported record_version: {manifest.record_version}", file=sys.stderr)  # noqa: T201
            return 2

        task = TaskSnapshot.model_validate_json((root / "task.json").read_text(encoding="utf-8"))
        if task.type != "scrape":
            print(f"v1 replay only supports scrape tasks, got {task.type}", file=sys.stderr)  # noqa: T201
            return 2

        hot_data = json.loads((root / "config.hot.json").read_text(encoding="utf-8"))
        hot = HotSettings.model_validate(hot_data)
        if manifest.redacted:
            print(  # noqa: T201
                "warning: record is redacted; cookies/api tokens/llm keys are placeholders; "
                "online replay may fail auth",
                file=sys.stderr,
            )

        payload = ScrapePayload.model_validate(task.payload)
        # 回放不接触用户媒体文件
        payload = payload.model_copy(update={"media_file_id": None})

        use_offline = (not online) and manifest.http_captured and (root / "http").is_dir()
        tmp = Path(tempfile.mkdtemp(prefix="amane-record-"))
        try:
            engine = await create_async_engine_from_path(tmp / "record.db")
            repo = Repository(engine)
            resource_store = ResourceStore(engine=engine, base_dir=tmp / "resources")

            # 可选: 注入 raw_cache, 以便覆盖 metadata cache 路径
            raw_path = root / "raw_cache.json"
            if raw_path.is_file() and CacheKind.metadata in payload.use_cache:
                raw = json.loads(raw_path.read_text(encoding="utf-8"))
                await repo.upsert_metadata(payload.number, raw=raw)

            r18_db = build_r18_db(hot.r18) if not use_offline else None
            browser: BrowserPool | None = None
            if use_offline:
                web: Any = ReplayWebClient(root / "http")
                http_client = HttpClient(web=web, browser=None)
                factory = CrawlerFactory(http_client, site_configs=hot.scraping.site_config, r18_db=None)
            else:
                stack = build_network_stack(hot, r18_db=r18_db)
                web = stack.web_client
                factory = stack.factory
                browser = stack.browser

            handler = ScrapeHandler(repo, factory, resource_store, hot, web)
            result = await handler.handle(payload)

            out = {
                "success": result.success,
                "error": result.error,
                "failed_sites": list(result.result.failed_sites) if result.result else [],
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))  # noqa: T201
            if isinstance(web, WebClient):
                await web.close()
            if browser is not None:
                await browser.close()
            if isinstance(r18_db, R18Database):
                await r18_db.close()
            await engine.dispose()
            return 0 if result.success else 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class _open_record:
    """接受目录或 zip, yield 含 manifest.json 的根目录."""

    def __init__(self, path: Path):
        self.path = path
        self._tmp: Path | None = None
        self._root: Path | None = None

    def __enter__(self) -> Path:
        if self.path.is_dir():
            root = self.path
            if not (root / "manifest.json").is_file():
                # zip 解压后可能多一层 task-N/
                candidates = list(root.glob("task-*/manifest.json"))
                if len(candidates) == 1:
                    root = candidates[0].parent
                else:
                    raise FileNotFoundError(f"manifest.json not found under {self.path}")
            self._root = root
            return root

        if self.path.is_file() and zipfile.is_zipfile(self.path):
            self._tmp = Path(tempfile.mkdtemp(prefix="amane-record-"))
            with zipfile.ZipFile(self.path) as zf:
                zf.extractall(self._tmp)
            manifests = list(self._tmp.rglob("manifest.json"))
            if not manifests:
                raise FileNotFoundError("zip has no manifest.json")
            self._root = manifests[0].parent
            return self._root

        raise FileNotFoundError(f"not a record dir or zip: {self.path}")

    def __exit__(self, *args: object) -> None:
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

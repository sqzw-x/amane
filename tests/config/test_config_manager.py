"""ConfigManager 测试 - ColdSettings, HotSettings, ConfigManager"""

import os
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from amane.app.bootstrap import build_safe_dirs
from amane.config import (
    SAFE_DIRS_ALLOW_ALL,
    ColdSettings,
    ConfigManager,
    DownloadableResource,
    HotSettings,
    ScrapingConfig,
    WorkerConfig,
)
from amane.config.manager import LANG_METADATA_FIELD_SET
from amane.enums import Language, MetadataField, SiteName
from amane.parsing import ContentType

# ---------------------------------------------------------------------------
# ColdSettings
# ---------------------------------------------------------------------------


class TestColdSettings:
    """ColdSettings: 仅环境变量, 不可变"""

    def test_defaults(self):
        """默认 data_dir 为 ./data"""
        with patch.dict(os.environ, {}, clear=False):
            # 移除可能干扰的 AMANE_ 环境变量
            env = {k: v for k, v in os.environ.items() if not k.startswith("AMANE_")}
            with patch.dict(os.environ, env, clear=True):
                s = ColdSettings()
                assert s.data_dir == Path("./data")
                assert s.supervised is False

    def test_supervised_env(self):
        with patch.dict(os.environ, {"AMANE_SUPERVISED": "1"}):
            assert ColdSettings().supervised is True

    def test_update_url_env(self):
        with patch.dict(os.environ, {"AMANE_UPDATE_URL": "http://127.0.0.1:18765/releases/latest"}):
            assert ColdSettings().update_url == "http://127.0.0.1:18765/releases/latest"

    def test_update_url_empty_env(self):
        with patch.dict(os.environ, {"AMANE_UPDATE_URL": ""}):
            assert ColdSettings().update_url is None

    def test_env_override(self, tmp_path: Path):
        """AMANE_DATA_DIR 环境变量覆盖默认值"""
        custom_dir = tmp_path / "custom_data"
        with patch.dict(os.environ, {"AMANE_DATA_DIR": str(custom_dir)}, clear=False):
            s = ColdSettings()
            assert s.data_dir == custom_dir

    def test_config_path_property(self, tmp_path: Path):
        """config_path 为 data_dir / config.toml"""
        with patch.dict(os.environ, {"AMANE_DATA_DIR": str(tmp_path)}, clear=False):
            s = ColdSettings()
            assert s.config_path == tmp_path / "config.toml"

    def test_db_path_property(self, tmp_path: Path):
        """db_path 为 data_dir / amane.db"""
        with patch.dict(os.environ, {"AMANE_DATA_DIR": str(tmp_path)}, clear=False):
            s = ColdSettings()
            assert s.db_path == tmp_path / "amane.db"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ALLOW_ALL", "ALLOW_ALL"),
            ("  ALLOW_ALL  ", "ALLOW_ALL"),
            ("", None),
            ("   ", None),
            ("allow_all", [Path("allow_all")]),
            ("ALLOW_ALL,/media", [Path("ALLOW_ALL"), Path("/media")]),
            ("/media,/data", [Path("/media"), Path("/data")]),
        ],
    )
    def test_parse_safe_dirs(self, raw: str, expected: object):
        """整段 ALLOW_ALL 才是哨兵; 大小写不同或夹在列表里都当普通路径."""
        with patch.dict(os.environ, {"AMANE_SAFE_DIRS": raw}):
            assert ColdSettings().safe_dirs == expected

    def test_parse_safe_dirs_rejects_non_path_value(self):
        with patch.dict(os.environ, {"AMANE_SAFE_DIRS": ""}), pytest.raises(ValidationError):
            ColdSettings(safe_dirs=123)  # type: ignore[arg-type]


class TestBuildSafeDirs:
    """build_safe_dirs: ALLOW_ALL / 显式路径 / library 回退."""

    def test_allow_all_is_unrestricted(self, tmp_path: Path):
        watch = tmp_path / "lib"
        watch.mkdir()
        with patch.dict(os.environ, {"AMANE_SAFE_DIRS": SAFE_DIRS_ALLOW_ALL}):
            assert build_safe_dirs(ColdSettings(), [str(watch)]) is None

    def test_explicit_existing_dirs(self, tmp_path: Path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        with patch.dict(os.environ, {"AMANE_SAFE_DIRS": f"{a},{b}"}):
            got = build_safe_dirs(ColdSettings(), [])
        assert got is not None
        assert set(got) == {a.resolve(), b.resolve()}

    def test_explicit_missing_dirs_dropped(self, tmp_path: Path):
        existing = tmp_path / "ok"
        existing.mkdir()
        missing = tmp_path / "gone"
        with patch.dict(os.environ, {"AMANE_SAFE_DIRS": f"{existing},{missing}"}):
            got = build_safe_dirs(ColdSettings(), [])
        assert got == [existing.resolve()]

    def test_explicit_all_missing_is_empty_not_unrestricted(self, tmp_path: Path):
        missing = tmp_path / "gone"
        with patch.dict(os.environ, {"AMANE_SAFE_DIRS": str(missing)}):
            assert build_safe_dirs(ColdSettings(), []) == []

    def test_unset_falls_back_to_library_paths(self, tmp_path: Path):
        lib = tmp_path / "lib"
        lib.mkdir()
        with patch.dict(os.environ, {"AMANE_SAFE_DIRS": ""}):
            got = build_safe_dirs(ColdSettings(), [str(lib), str(tmp_path / "missing")])
        assert got == [lib.resolve()]

    def test_unset_no_libraries_is_empty(self):
        with patch.dict(os.environ, {"AMANE_SAFE_DIRS": ""}):
            assert build_safe_dirs(ColdSettings(), []) == []


# ---------------------------------------------------------------------------
# HotSettings
# ---------------------------------------------------------------------------


class TestHotSettings:
    """HotSettings: 嵌套结构, 带验证, extra=forbid"""

    def test_unknown_field_rejected(self):
        """未知字段应抛出 ValidationError"""
        with pytest.raises(ValidationError, match=r"Extra inputs are not permitted"):
            HotSettings(unknown_field="value")  # type: ignore[call-arg]

    def test_concurrency_too_low(self):
        """worker.concurrency < 1 被拒绝"""
        with pytest.raises(ValidationError, match=r"greater than or equal to 1"):
            HotSettings(worker=WorkerConfig(concurrency=0))

    def test_poll_interval_too_low(self):
        """worker.poll_interval < 0.1 被拒绝"""
        with pytest.raises(ValidationError, match=r"greater than or equal to 0.1"):
            HotSettings(worker=WorkerConfig(poll_interval=0.01))

    def test_poll_interval_too_high(self):
        """worker.poll_interval > 10.0 被拒绝"""
        with pytest.raises(ValidationError, match=r"less than or equal to 10"):
            HotSettings(worker=WorkerConfig(poll_interval=11.0))


class TestScrapingDownloadResources:
    """download_resources 默认与旧字段迁移."""

    def test_default_all_kinds(self):
        cfg = ScrapingConfig()
        assert cfg.download_resources == [
            DownloadableResource.thumb,
            DownloadableResource.poster,
            DownloadableResource.extrafanart,
        ]

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (
                {"download_thumb": True, "download_extrafanart": True},
                [
                    DownloadableResource.thumb,
                    DownloadableResource.poster,
                    DownloadableResource.trailer,
                    DownloadableResource.extrafanart,
                ],
            ),
            (
                {"download_thumb": False, "download_extrafanart": True},
                [DownloadableResource.extrafanart],
            ),
            (
                {"download_thumb": True, "download_extrafanart": False},
                [
                    DownloadableResource.thumb,
                    DownloadableResource.poster,
                    DownloadableResource.trailer,
                ],
            ),
            (
                {"download_thumb": False, "download_extrafanart": False},
                [],
            ),
        ],
    )
    def test_legacy_bool_migration(self, payload: dict, expected: list[DownloadableResource]):
        cfg = ScrapingConfig.model_validate(payload)
        assert cfg.download_resources == expected

    def test_removed_fields_ignored(self):
        cfg = ScrapingConfig.model_validate({"skip_existing": False, "write_nfo": False})
        assert "skip_existing" not in type(cfg).model_fields
        assert "write_nfo" not in type(cfg).model_fields


class TestDebugCaptureMigration:
    def test_scraping_debug_capture_moves_to_logging(self):
        cfg = HotSettings.model_validate({"scraping": {"debug_capture": True}})
        assert cfg.logging.debug_capture is True

    def test_logging_debug_capture_wins_if_both_present(self):
        cfg = HotSettings.model_validate({"scraping": {"debug_capture": True}, "logging": {"debug_capture": False}})
        assert cfg.logging.debug_capture is False


class TestScrapingPriorityMigration:
    def test_default_priority_reorders_routes(self):
        cfg = ScrapingConfig.model_validate(
            {
                "default_priority": ["javbus", "javdb"],
                "content_routes": {"censored": ["javdb", "dmm", "javbus"]},
            }
        )
        assert cfg.content_routes[ContentType.CENSORED] == [SiteName.JAVBUS, SiteName.JAVDB, SiteName.DMM]

    def test_strips_empty_field_priority(self):
        cfg = ScrapingConfig.model_validate({"field_priority": {"title": [], "plot": ["dmm"]}})
        assert MetadataField.TITLE not in cfg.field_priority
        assert cfg.field_priority[MetadataField.PLOT] == [SiteName.DMM]

    def test_no_default_priority_keeps_route_order(self):
        cfg = ScrapingConfig.model_validate({"content_routes": {"censored": ["javbus", "javdb"]}})
        assert cfg.content_routes[ContentType.CENSORED] == [SiteName.JAVBUS, SiteName.JAVDB]
        assert set(cfg.content_routes) == set(ContentType)
        assert cfg.content_routes[ContentType.FC2] == ScrapingConfig().content_routes[ContentType.FC2]

    def test_default_is_sparse(self):
        assert ScrapingConfig().field_priority == {}

    def test_default_routes_type_specific_heads(self):
        routes = ScrapingConfig().content_routes
        assert routes[ContentType.AMATEUR][0] == SiteName.MGSTAGE
        assert routes[ContentType.CHINESE][0] == SiteName.IQQTV
        assert routes[ContentType.HENTAI][0] == SiteName.GETCHU
        assert routes[ContentType.WESTERN][0] == SiteName.THEPORNDB
        assert SiteName.AVSOX in routes[ContentType.UNCENSORED]
        assert SiteName.FC2PPVDB in routes[ContentType.FC2]
        assert SiteName.OFFICIAL in routes[ContentType.CENSORED]


class TestFrozenKeyDictCompleteness:
    """x-frozen-keys 全量 dict 缺 key 必须按代码枚举补齐, 未知 key 丢弃."""

    def test_defaults_cover_canonical_keys(self):
        cfg = ScrapingConfig()
        assert set(cfg.site_config) == set(SiteName)
        assert set(cfg.content_routes) == set(ContentType)
        assert MetadataField.TITLE in cfg.field_language
        assert set(cfg.field_language) == LANG_METADATA_FIELD_SET

    @pytest.mark.parametrize(
        ("payload", "javdb_proxy", "javdb_rate"),
        [
            ({"javdb": {"use_proxy": False}}, False, 2),
            ({}, True, 2),
            ({"gone": {"use_proxy": False}, "javdb": {"rate_limit": 1.0}}, True, 1.0),
        ],
    )
    def test_site_config_partial_or_unknown_fills_all_sites(
        self, payload: dict, javdb_proxy: bool, javdb_rate: float
    ) -> None:
        cfg = ScrapingConfig.model_validate({"site_config": payload})
        assert set(cfg.site_config) == set(SiteName)
        assert cfg.site_config[SiteName.JAVDB].use_proxy is javdb_proxy
        assert cfg.site_config[SiteName.JAVDB].rate_limit == javdb_rate
        assert cfg.site_config[SiteName.GFRIENDS].use_proxy is True

    @pytest.mark.parametrize(
        ("payload", "censored"),
        [
            ({"censored": ["javbus"]}, [SiteName.JAVBUS]),
            ({}, None),
            ({"gone": ["javdb"], "censored": ["javbus", "dmm"]}, [SiteName.JAVBUS, SiteName.DMM]),
        ],
    )
    def test_content_routes_partial_or_unknown_fills_all_types(
        self, payload: dict, censored: list[SiteName] | None
    ) -> None:
        cfg = ScrapingConfig.model_validate({"content_routes": payload})
        defaults = ScrapingConfig()
        assert set(cfg.content_routes) == set(ContentType)
        expected = defaults.content_routes[ContentType.CENSORED] if censored is None else censored
        assert cfg.content_routes[ContentType.CENSORED] == expected
        assert cfg.content_routes[ContentType.FC2] == defaults.content_routes[ContentType.FC2]

    def test_content_routes_empty_list_kept(self):
        cfg = ScrapingConfig.model_validate({"content_routes": {"fc2": []}})
        assert cfg.content_routes[ContentType.FC2] == []
        assert cfg.content_routes[ContentType.CENSORED] == ScrapingConfig().content_routes[ContentType.CENSORED]

    @pytest.mark.parametrize(
        ("payload", "title_lang"),
        [
            ({"title": "jp"}, Language.JP),
            ({}, Language.ZH_CN),
            ({"gone": "en", "score": "en", "title": "jp"}, Language.JP),
        ],
    )
    def test_field_language_partial_or_unknown_fills_lang_fields(self, payload: dict, title_lang: Language) -> None:
        cfg = ScrapingConfig.model_validate({"field_language": payload})
        defaults = ScrapingConfig()
        assert set(cfg.field_language) == set(defaults.field_language)
        assert cfg.field_language[MetadataField.TITLE] == title_lang
        assert cfg.field_language[MetadataField.PLOT] == Language.ZH_CN
        assert MetadataField.SCORE not in cfg.field_language

    @pytest.mark.parametrize("field", ["site_config", "content_routes", "field_language"])
    def test_non_dict_rejected(self, field: str):
        with pytest.raises(ValidationError):
            ScrapingConfig.model_validate({field: "javdb"})


# ---------------------------------------------------------------------------
# ConfigManager.update()
# ---------------------------------------------------------------------------


class TestConfigManagerUpdate:
    """ConfigManager.update() - patch 合并语义"""

    @pytest.fixture
    def mgr(self, tmp_path: Path) -> ConfigManager:
        """使用 tmp_path 作为 data_dir 的新 ConfigManager"""
        with patch.dict(os.environ, {"AMANE_DATA_DIR": str(tmp_path)}, clear=False):
            cold = ColdSettings()
        return ConfigManager.with_cold(cold)

    def test_single_field(self, mgr: ConfigManager):
        """更新单个字段"""
        mgr.update({"network": {"proxy": "socks5://localhost:1080"}})
        assert mgr.hot.network.proxy == "socks5://localhost:1080"

    def test_multiple_fields_same_section(self, mgr: ConfigManager):
        """更新同一 section 中的多个字段"""
        mgr.update({"scraping": {"download_resources": [], "crop_poster": False}})
        assert mgr.hot.scraping.download_resources == []
        assert mgr.hot.scraping.crop_poster is False
        # 其他字段保持不变
        assert mgr.hot.scraping.jpeg_quality == 95

    def test_multiple_sections(self, mgr: ConfigManager):
        """跨多个 section 更新字段"""
        mgr.update(
            {
                "network": {"proxy": "http://proxy:8080"},
                "worker": {"concurrency": 8},
            }
        )
        assert mgr.hot.network.proxy == "http://proxy:8080"
        assert mgr.hot.worker.concurrency == 8

    def test_empty_patch_noop(self, mgr: ConfigManager):
        """空 patch 不产生任何变化"""
        original = mgr.hot.model_dump()
        mgr.update({})
        assert mgr.hot.model_dump() == original

    def test_invalid_value_rejected(self, mgr: ConfigManager):
        """无效值应抛出 ValidationError, 状态保持不变"""
        original_concurrency = mgr.hot.worker.concurrency
        with pytest.raises(ValidationError):
            mgr.update({"worker": {"concurrency": 99}})
        # 更新失败后状态不变
        assert mgr.hot.worker.concurrency == original_concurrency

    def test_type_coercion(self, mgr: ConfigManager):
        """字符串值由 Pydantic 强制转换为正确类型"""
        mgr.update({"worker": {"concurrency": "5"}})
        assert mgr.hot.worker.concurrency == 5
        assert isinstance(mgr.hot.worker.concurrency, int)

    def test_null_clears_optional(self, mgr: ConfigManager):
        """设置 None 可清除 optional 字段"""
        mgr.update({"network": {"proxy": "http://x"}})
        assert mgr.hot.network.proxy == "http://x"
        mgr.update({"network": {"proxy": None}})
        assert mgr.hot.network.proxy is None

    def test_empty_section_noop(self, mgr: ConfigManager):
        """空 section 字典不产生任何变化"""
        original = mgr.hot.model_dump()
        mgr.update({"network": {}})
        assert mgr.hot.model_dump() == original

    def test_sequential_updates_accumulate(self, mgr: ConfigManager):
        """多次顺序更新累积变更"""
        mgr.update({"network": {"proxy": "http://a"}})
        mgr.update({"worker": {"concurrency": 6}})
        mgr.update({"scraping": {"crop_poster": False}})

        assert mgr.hot.network.proxy == "http://a"
        assert mgr.hot.worker.concurrency == 6
        assert mgr.hot.scraping.crop_poster is False

    def test_persists_to_toml(self, mgr: ConfigManager):
        """更新会写入 TOML 文件"""
        mgr.update({"network": {"proxy": "socks5://127.0.0.1:7890"}})

        config_path = mgr.cold.config_path
        assert config_path.exists()

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        assert data["network"]["proxy"] == "socks5://127.0.0.1:7890"

    def test_unknown_section_ignored(self, mgr: ConfigManager):
        """未知 section 被 Pydantic extra=forbid 拒绝"""
        with pytest.raises(ValidationError):
            mgr.update({"nonexistent_section": {"key": "value"}})


# ---------------------------------------------------------------------------
# ConfigManager.load()
# ---------------------------------------------------------------------------


class TestConfigManagerLoad:
    """ConfigManager.load() - 文件创建, 读取"""

    def test_creates_default_toml_if_missing(self, tmp_path: Path):
        """config.toml 不存在时, 使用默认值创建"""
        with patch.dict(os.environ, {"AMANE_DATA_DIR": str(tmp_path)}, clear=False):
            cold = ColdSettings()

        ConfigManager.with_cold(cold)

        config_path = tmp_path / "config.toml"
        assert config_path.exists()

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        # 默认值不写入 TOML, 因此文件内容应为空
        assert data == {}

    def test_reads_existing_toml(self, tmp_path: Path):
        """从已有 TOML 文件读取配置值"""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[network]\nproxy = "http://myproxy:3128"\n\n[worker]\nconcurrency = 10\n')

        with patch.dict(os.environ, {"AMANE_DATA_DIR": str(tmp_path)}, clear=False):
            cold = ColdSettings()

        mgr = ConfigManager.with_cold(cold)

        assert mgr.hot.network.proxy == "http://myproxy:3128"
        assert mgr.hot.worker.concurrency == 10
        # 未指定的字段使用默认值
        assert mgr.hot.scraping.crop_poster is True

    def test_legacy_partial_frozen_dicts_fill_canonical_keys(self, tmp_path: Path):
        """已有 frozen dict 但缺 key 时, 加载后按代码枚举补齐."""
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            "[scraping.site_config.javdb]\n"
            "use_proxy = false\n"
            "\n"
            "[scraping.content_routes]\n"
            'censored = ["javbus"]\n'
            "\n"
            "[scraping.field_language]\n"
            'title = "jp"\n'
        )

        with patch.dict(os.environ, {"AMANE_DATA_DIR": str(tmp_path)}, clear=False):
            cold = ColdSettings()

        mgr = ConfigManager.with_cold(cold)
        scraping = mgr.hot.scraping
        defaults = ScrapingConfig()
        assert set(scraping.site_config) == set(SiteName)
        assert scraping.site_config[SiteName.JAVDB].use_proxy is False
        assert scraping.site_config[SiteName.GFRIENDS].use_proxy is True
        assert set(scraping.content_routes) == set(ContentType)
        assert scraping.content_routes[ContentType.CENSORED] == [SiteName.JAVBUS]
        assert scraping.content_routes[ContentType.FC2] == defaults.content_routes[ContentType.FC2]
        assert set(scraping.field_language) == set(defaults.field_language)
        assert scraping.field_language[MetadataField.TITLE] == Language.JP
        assert scraping.field_language[MetadataField.PLOT] == Language.ZH_CN

    def test_legacy_paths_section_dropped(self, tmp_path: Path):
        """旧 TOML 的 [paths] 被忽略, extra=forbid 不因此失败."""
        config_path = tmp_path / "config.toml"
        config_path.write_text('[paths]\nmove_mode = "hardlink"\n\n[worker]\nconcurrency = 4\n')

        with patch.dict(os.environ, {"AMANE_DATA_DIR": str(tmp_path)}, clear=False):
            cold = ColdSettings()

        mgr = ConfigManager.with_cold(cold)
        assert mgr.hot.worker.concurrency == 4

    def test_round_trip_preserves_values(self, tmp_path: Path):
        """load → update → load 往返保留所有值"""
        with patch.dict(os.environ, {"AMANE_DATA_DIR": str(tmp_path)}, clear=False):
            cold = ColdSettings()

        mgr1 = ConfigManager.with_cold(cold)
        mgr1.update(
            {
                "network": {"proxy": "socks5://roundtrip:1080"},
                "worker": {"concurrency": 7, "poll_interval": 5.0},
                "scraping": {"crop_poster": False},
                "watcher": {"use_polling": True},
            }
        )

        # 从相同路径重新加载
        mgr2 = ConfigManager.with_cold(cold)

        assert mgr2.hot.network.proxy == "socks5://roundtrip:1080"
        assert mgr2.hot.worker.concurrency == 7
        assert mgr2.hot.worker.poll_interval == 5.0
        assert mgr2.hot.scraping.crop_poster is False
        assert mgr2.hot.watcher.use_polling is True

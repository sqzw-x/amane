"""redact_hot 表测试."""

import json

import pytest

from amane.config.manager import HotSettings
from amane.observability.models import REDACTION_PLACEHOLDER
from amane.observability.redact import hot_slice_for_task, needs_secrets_file, redact_dsn, redact_hot, redact_proxy

# 每个凭据字段一个唯一标记串, 便于在序列化结果里定位漏脱敏的字段.
MARKERS: tuple[str, ...] = (
    "AGENT-MARKER",
    "LLM-MARKER",
    "MEDIA-MARKER",
    "COOKIE-MARKER",
    "TOKEN-MARKER",
    "SITE-PROXY-MARKER",
    "DSN-MARKER",
    "READ-PW-MARKER",
    "GLOBAL-PROXY-MARKER",
)


def _dig(data: dict, path: str):
    """按点分路径取值; 路径不存在时由 KeyError 暴露写错的用例."""
    for key in path.split("."):
        data = data[key]
    return data


def _leaves(data: dict, prefix: str = "") -> dict[str, object]:
    """按点分路径展开叶子, 用于逐项比对原值与脱敏值."""
    out: dict[str, object] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_leaves(value, path))
        else:
            out[path] = value
    return out


@pytest.fixture
def secret_hot() -> HotSettings:
    """所有携带凭据的字段各填一个标记串."""
    hot = HotSettings()
    hot.agent.api_key = MARKERS[0]
    hot.llm.api_key = MARKERS[1]
    hot.media_server.api_key = MARKERS[2]
    hot.scraping.site_config["javbus"].cookie = {"dv": MARKERS[3]}
    hot.scraping.site_config["theporndb"].api_token = MARKERS[4]
    hot.scraping.site_config["javbus"].proxy = f"http://u:{MARKERS[5]}@host:1"
    hot.r18.dsn = f"postgresql://u:{MARKERS[6]}@host/db"
    hot.r18.read_password = MARKERS[7]
    hot.network.proxy = f"http://u:{MARKERS[8]}@host:1"
    return hot


@pytest.mark.parametrize(
    ("dump", "expected"),
    [
        pytest.param({}, False, id="空配置"),
        pytest.param({"scraping": {"site_config": {"javdb": {"cookie": {}}}}}, False, id="空 cookie"),
        pytest.param({"scraping": {"site_config": {"javdb": {"cookie": {"a": "b"}}}}}, True, id="有 cookie"),
        pytest.param({"llm": {"api_key": "k"}}, True, id="llm 密钥"),
        pytest.param({"llm": {"api_key": None}}, False, id="llm 密钥为空"),
        pytest.param({"media_server": {"api_key": "m"}}, True, id="媒体服务器密钥"),
        pytest.param({"media_server": {"api_key": None}}, False, id="媒体服务器密钥为空"),
        pytest.param({"r18": {"dsn": "postgresql://u:p@h/db"}}, True, id="r18 dsn"),
        pytest.param({"r18": {"read_password": "default"}}, False, id="默认读密码不算密钥"),
        pytest.param({"network": {"proxy": "http://u:p@h:1"}}, True, id="代理带凭据"),
        pytest.param({"network": {"proxy": "http://h:1"}}, False, id="代理不带凭据"),
        pytest.param({"agent": {"api_key": "a"}}, True, id="助手密钥"),
        pytest.param({"plugins": {"acme.fake": {"config": {"api_token": "secret"}}}}, True, id="插件密钥"),
    ],
)
def test_needs_secrets_file(dump: dict, expected: bool):
    assert needs_secrets_file(dump) is expected


@pytest.mark.parametrize(
    ("task_type", "expected"),
    [
        pytest.param("scrape", {"scraping", "network", "llm", "r18", "plugins"}, id="刮削只留参与回放的段"),
        pytest.param("organize", {"scraping", "network", "llm", "r18", "plugins"}, id="其余任务只剔噪声段"),
    ],
)
def test_hot_slice_drops_noise(task_type: str, expected: set[str]):
    full = {
        "scraping": {"a": 1},
        "network": {"b": 2},
        "llm": {},
        "r18": {},
        "plugins": {},
        "watcher": {"x": 1},
        "worker": {"y": 1},
        "logging": {"level": "INFO"},
    }
    assert set(hot_slice_for_task(full, task_type)) == expected


@pytest.mark.parametrize(
    ("hot", "path", "expected"),
    [
        pytest.param(
            {"scraping": {"site_config": {"javdb": {"cookie": {"session": "s", "dv": "1"}}}}},
            "scraping.site_config.javdb.cookie",
            {"session": REDACTION_PLACEHOLDER, "dv": REDACTION_PLACEHOLDER},
            id="cookie 值脱敏且保留键",
        ),
        pytest.param(
            {"scraping": {"site_config": {"javdb": {"cookie": {}}}}},
            "scraping.site_config.javdb.cookie",
            {},
            id="空 cookie 不动",
        ),
        pytest.param(
            {"scraping": {"site_config": {"theporndb": {"api_token": "tok_abc"}}}},
            "scraping.site_config.theporndb.api_token",
            REDACTION_PLACEHOLDER,
            id="站点密钥",
        ),
        pytest.param(
            {"scraping": {"site_config": {"theporndb": {"api_token": None}}}},
            "scraping.site_config.theporndb.api_token",
            None,
            id="站点密钥为空",
        ),
        pytest.param(
            {"scraping": {"site_config": {"javdb": {"proxy": "http://user:secret@127.0.0.1:7890"}}}},
            "scraping.site_config.javdb.proxy",
            f"http://{REDACTION_PLACEHOLDER}@127.0.0.1:7890",
            id="站点代理留占位符",
        ),
        pytest.param(
            {"scraping": {"site_config": {"javdb": {"proxy": None}}}},
            "scraping.site_config.javdb.proxy",
            None,
            id="站点代理为空",
        ),
        pytest.param({"llm": {"api_key": "sk-live"}}, "llm.api_key", REDACTION_PLACEHOLDER, id="llm 密钥"),
        pytest.param({"llm": {"api_key": None}}, "llm.api_key", None, id="llm 密钥为空"),
        pytest.param({"llm": {"enabled": True}}, "llm.enabled", True, id="llm 开关不动"),
        pytest.param(
            {"media_server": {"kind": "jellyfin", "url": "http://media.local", "api_key": "live"}},
            "media_server.api_key",
            REDACTION_PLACEHOLDER,
            id="媒体服务器密钥",
        ),
        pytest.param(
            {"media_server": {"url": "http://media.local"}},
            "media_server.url",
            "http://media.local",
            id="媒体服务器地址不动",
        ),
        pytest.param({"agent": {"api_key": "sk-agent"}}, "agent.api_key", REDACTION_PLACEHOLDER, id="助手密钥"),
        pytest.param({"agent": {"model": "gpt-4o-mini"}}, "agent.model", "gpt-4o-mini", id="助手模型不动"),
        pytest.param(
            {"network": {"proxy": "socks5://user:pass@127.0.0.1:7890"}},
            "network.proxy",
            f"socks5://{REDACTION_PLACEHOLDER}@127.0.0.1:7890",
            id="全局代理留占位符",
        ),
        pytest.param(
            {"network": {"proxy": "http://127.0.0.1:8080"}},
            "network.proxy",
            "http://127.0.0.1:8080",
            id="代理无凭据不动",
        ),
        pytest.param({"network": {"proxy": None}}, "network.proxy", None, id="代理为空"),
        pytest.param(
            {"r18": {"dsn": "postgresql://admin:s3cret@db:5432/postgres"}},
            "r18.dsn",
            f"postgresql://admin:{REDACTION_PLACEHOLDER}@db:5432/postgres",
            id="dsn 保留用户名脱密码",
        ),
        pytest.param(
            {"r18": {"read_password": "ro_pass"}}, "r18.read_password", REDACTION_PLACEHOLDER, id="r18 读密码"
        ),
        pytest.param(
            {"plugins": {"acmefake": {"config": {"api_token": "secret"}}}},
            "plugins.acmefake.config.api_token",
            REDACTION_PLACEHOLDER,
            id="插件密钥",
        ),
        pytest.param(
            {"plugins": {"acmefake": {"config": {"endpoint": "https://example.test"}}}},
            "plugins.acmefake.config.endpoint",
            "https://example.test",
            id="插件普通配置不动",
        ),
    ],
)
def test_redact_hot(hot: dict, path: str, expected: object):
    assert _dig(redact_hot(hot), path) == expected


def test_redact_hot_does_not_mutate_input():
    original = {
        "scraping": {"site_config": {"javdb": {"cookie": {"a": "b"}, "api_token": "t", "proxy": "http://u:p@h:1"}}},
        "llm": {"api_key": "k"},
        "network": {"proxy": "http://u:p@h:1"},
    }
    snapshot = json.loads(json.dumps(original))
    redact_hot(original)
    assert original == snapshot


@pytest.mark.parametrize(
    ("proxy", "expected"),
    [
        pytest.param("socks5://u:p@host:1", f"socks5://{REDACTION_PLACEHOLDER}@host:1", id="userinfo 留占位符"),
        pytest.param("http://user@host:80/path", f"http://{REDACTION_PLACEHOLDER}@host:80/path", id="仅用户名"),
        pytest.param("http://host", "http://host", id="无凭据不动"),
        pytest.param("socks5://[::1]:1080", "socks5://[::1]:1080", id="IPv6 无凭据不动"),
        pytest.param("socks5://u:p@[::1]:1080", f"socks5://{REDACTION_PLACEHOLDER}@[::1]:1080", id="IPv6 带凭据"),
        pytest.param("u:p@host:1", REDACTION_PLACEHOLDER, id="解析不出主机整体打码"),
        pytest.param("", REDACTION_PLACEHOLDER, id="空串整体打码"),
    ],
)
def test_redact_proxy(proxy: str, expected: str):
    assert redact_proxy(proxy) == expected


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        pytest.param(
            "postgresql://u:secret@localhost/db", f"postgresql://u:{REDACTION_PLACEHOLDER}@localhost/db", id="脱密码"
        ),
        pytest.param("postgresql://u@localhost/db", "postgresql://u@localhost/db", id="无密码不动"),
        pytest.param("not a dsn", REDACTION_PLACEHOLDER, id="解析失败整体打码"),
    ],
)
def test_redact_dsn(dsn: str, expected: str):
    assert redact_dsn(dsn) == expected


@pytest.mark.parametrize("task_type", ["scrape", "organize", "upscale", "actor_scrape"])
def test_record_leaks_no_credential(secret_hot: HotSettings, task_type: str):
    """凭据字段漏脱敏时, 标记串会出现在导出记录里."""
    dump = secret_hot.model_dump(mode="json")
    text = json.dumps(redact_hot(hot_slice_for_task(dump, task_type)), ensure_ascii=False)
    assert sorted(marker for marker in MARKERS if marker in text) == []


def test_rewritten_values_carry_placeholder(secret_hot: HotSettings):
    """凡被改写的值都要留占位符: 与合法配置长得一样的输出会让读者误以为那就是原值."""
    original = _leaves(secret_hot.model_dump(mode="json"))
    redacted = _leaves(redact_hot(secret_hot.model_dump(mode="json")))
    changed = {path for path, value in original.items() if redacted[path] != value}
    assert changed, "夹具未触发脱敏"
    missing = sorted(path for path in changed if REDACTION_PLACEHOLDER not in str(redacted[path]))
    assert missing == [], "改写后未留占位符"


def test_credential_free_values_untouched(secret_hot: HotSettings):
    """没有凭据的配置原样保留, 不因脱敏多出标记."""
    original = _leaves(secret_hot.model_dump(mode="json"))
    redacted = _leaves(redact_hot(secret_hot.model_dump(mode="json")))
    changed = {path for path, value in original.items() if redacted[path] != value}
    untouched = {
        "network.browser.solver_url",
        "scraping.poster_ratio",
        "scraping.site_config.javbus.use_browser",
        "r18.read_user",
    }
    assert untouched <= set(original)
    assert changed & untouched == set(), "无凭据字段被改动"

"""redact_hot 表测试."""

import pytest

from amane.observability.models import REDACTION_PLACEHOLDER
from amane.observability.redact import hot_slice_for_task, needs_secrets_file, redact_dsn, redact_hot, redact_proxy


def test_hot_slice_scrape_drops_noise():
    full = {
        "scraping": {"a": 1},
        "network": {"b": 2},
        "llm": {},
        "r18": {},
        "plugins": {},
        "watcher": {"x": 1},
        "worker": {"y": 1},
        "logging": {"level": "INFO"},
        "sr": {},
    }
    assert set(hot_slice_for_task(full, "scrape").keys()) == {"scraping", "network", "llm", "r18", "plugins"}


@pytest.mark.parametrize(
    ("dump", "expected"),
    [
        ({"scraping": {"site_config": {"javdb": {"cookie": {}}}}, "llm": {}, "network": {}, "r18": {}}, False),
        ({"scraping": {"site_config": {"javdb": {"cookie": {"a": "b"}}}}, "llm": {}, "network": {}, "r18": {}}, True),
        ({"scraping": {"site_config": {}}, "llm": {"api_key": "k"}, "network": {}, "r18": {}}, True),
        ({"scraping": {"site_config": {}}, "llm": {}, "network": {}, "r18": {"dsn": "postgresql://u:p@h/db"}}, True),
        ({"scraping": {"site_config": {}}, "llm": {}, "network": {}, "r18": {"read_password": "default"}}, False),
        ({"plugins": {"acme.fake": {"config": {"api_token": "secret"}}}}, True),
        ({"emby": {"url": "http://emby.local:8096", "api_key": "k"}}, True),
        ({"emby": {"url": "http://emby.local:8096", "api_key": None}}, False),
    ],
)
def test_needs_secrets_file(dump: dict, expected: bool):
    assert needs_secrets_file(dump) is expected


@pytest.mark.parametrize(
    ("input_hot", "assertions"),
    [
        pytest.param(
            {
                "scraping": {
                    "site_config": {
                        "javdb": {"cookie": {"session": "secret", "dv": "1"}, "api_token": None},
                    }
                }
            },
            lambda out: (
                out["scraping"]["site_config"]["javdb"]["cookie"] == {"session": "***", "dv": "***"}
                and out["scraping"]["site_config"]["javdb"]["api_token"] is None
            ),
            id="cookie-values-redacted-keys-kept",
        ),
        pytest.param(
            {"scraping": {"site_config": {"theporndb": {"cookie": {}, "api_token": "tok_abc"}}}},
            lambda out: out["scraping"]["site_config"]["theporndb"]["api_token"] == REDACTION_PLACEHOLDER,
            id="api-token-redacted",
        ),
        pytest.param(
            {"scraping": {"site_config": {"javdb": {"cookie": {}}}}},
            lambda out: out["scraping"]["site_config"]["javdb"]["cookie"] == {},
            id="empty-cookie-unchanged",
        ),
        pytest.param(
            {"llm": {"api_key": "sk-live", "enabled": True}},
            lambda out: out["llm"]["api_key"] == REDACTION_PLACEHOLDER and out["llm"]["enabled"] is True,
            id="llm-api-key",
        ),
        pytest.param({"llm": {"api_key": None}}, lambda out: out["llm"]["api_key"] is None, id="llm-api-key-null"),
        pytest.param(
            {"emby": {"url": "http://emby.local:8096", "api_key": "k"}},
            lambda out: (
                out["emby"]["api_key"] == REDACTION_PLACEHOLDER and out["emby"]["url"] == "http://emby.local:8096"
            ),
            id="emby-api-key",
        ),
        pytest.param(
            {"network": {"proxy": "socks5://user:pass@127.0.0.1:7890"}},
            lambda out: out["network"]["proxy"] == "socks5://127.0.0.1:7890",
            id="proxy-strip-userinfo",
        ),
        pytest.param(
            {"network": {"proxy": "http://127.0.0.1:8080"}},
            lambda out: out["network"]["proxy"] == "http://127.0.0.1:8080",
            id="proxy-no-userinfo",
        ),
        pytest.param({"network": {"proxy": None}}, lambda out: out["network"]["proxy"] is None, id="proxy-null"),
        pytest.param(
            {
                "r18": {
                    "dsn": "postgresql://admin:s3cret@db:5432/postgres",
                    "read_password": "ro_pass",
                }
            },
            lambda out: (
                out["r18"]["read_password"] == REDACTION_PLACEHOLDER
                and "***" in out["r18"]["dsn"]
                and "s3cret" not in out["r18"]["dsn"]
            ),
            id="r18-dsn-and-password",
        ),
        pytest.param(
            {"plugins": {"acme.fake": {"config": {"api_token": "secret", "endpoint": "https://example.test"}}}},
            lambda out: (
                out["plugins"]["acme.fake"]["config"]["api_token"] == REDACTION_PLACEHOLDER
                and out["plugins"]["acme.fake"]["config"]["endpoint"] == "https://example.test"
            ),
            id="plugin-secret",
        ),
    ],
)
def test_redact_hot(input_hot: dict, assertions):
    out = redact_hot(input_hot)
    assert assertions(out)


def test_redact_hot_does_not_mutate_input():
    original = {
        "scraping": {"site_config": {"javdb": {"cookie": {"a": "b"}, "api_token": "t"}}},
        "llm": {"api_key": "k"},
    }
    redact_hot(original)
    assert original["scraping"]["site_config"]["javdb"]["cookie"]["a"] == "b"
    assert original["llm"]["api_key"] == "k"


@pytest.mark.parametrize(
    ("proxy", "expected"),
    [
        ("socks5://u:p@host:1", "socks5://host:1"),
        ("http://host", "http://host"),
        ("http://user@host:80/path", "http://host:80/path"),
    ],
)
def test_redact_proxy(proxy: str, expected: str):
    assert redact_proxy(proxy) == expected


def test_redact_dsn_hides_password():
    assert "secret" not in redact_dsn("postgresql://u:secret@localhost/db")
    assert "localhost" in redact_dsn("postgresql://u:secret@localhost/db")

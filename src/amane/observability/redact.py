"""HotSettings 脱敏与任务记录用配置切片."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.engine.url import make_url

from .models import REDACTION_PLACEHOLDER

# scrape 回放只需这些 section; watcher/worker/logging/sr 对解析无贡献
_SCRAPE_HOT_KEYS: tuple[str, ...] = ("scraping", "network", "llm", "r18", "plugins")


def hot_slice_for_task(hot_dump: dict[str, Any], task_type: str) -> dict[str, Any]:
    """去掉与回放无关的噪声 section."""
    if task_type == "scrape":
        return {k: deepcopy(hot_dump[k]) for k in _SCRAPE_HOT_KEYS if k in hot_dump}
    drop = {"watcher", "worker", "logging"}
    return {k: deepcopy(v) for k, v in hot_dump.items() if k not in drop}


def needs_secrets_file(hot_dump: dict[str, Any]) -> bool:
    """默认 r18 密码不算需旁路保存的明文密钥."""
    scraping = hot_dump.get("scraping")
    if isinstance(scraping, dict):
        site_config = scraping.get("site_config")
        if isinstance(site_config, dict):
            for site_cfg in site_config.values():
                if not isinstance(site_cfg, dict):
                    continue
                if site_cfg.get("cookie") or site_cfg.get("api_token"):
                    return True
    llm = hot_dump.get("llm")
    if isinstance(llm, dict) and llm.get("api_key"):
        return True
    emby = hot_dump.get("emby")
    if isinstance(emby, dict) and emby.get("api_key"):
        return True
    network = hot_dump.get("network")
    if isinstance(network, dict) and network.get("proxy"):
        proxy = str(network["proxy"])
        # userinfo@host
        after_scheme = proxy.split("://", 1)[-1]
        if "@" in after_scheme.split("/", 1)[0]:
            return True
    r18 = hot_dump.get("r18")
    if isinstance(r18, dict) and r18.get("dsn"):
        return True

    plugins = hot_dump.get("plugins")
    if isinstance(plugins, dict):
        for plugin in plugins.values():
            if isinstance(plugin, dict) and _has_secret_value(plugin.get("config")):
                return True
    return False


def redact_hot(data: dict[str, Any]) -> dict[str, Any]:
    """返回 HotSettings model_dump 的脱敏副本.

    空值保持 null/{}; 有值的密钥类字段替换为 ``***``.
    """
    out = deepcopy(data)

    scraping = out.get("scraping")
    if isinstance(scraping, dict):
        site_config = scraping.get("site_config")
        if isinstance(site_config, dict):
            for site_cfg in site_config.values():
                if not isinstance(site_cfg, dict):
                    continue
                cookie = site_cfg.get("cookie")
                if isinstance(cookie, dict) and cookie:
                    site_cfg["cookie"] = dict.fromkeys(cookie, REDACTION_PLACEHOLDER)
                if site_cfg.get("api_token"):
                    site_cfg["api_token"] = REDACTION_PLACEHOLDER

    llm = out.get("llm")
    if isinstance(llm, dict) and llm.get("api_key"):
        llm["api_key"] = REDACTION_PLACEHOLDER

    emby = out.get("emby")
    if isinstance(emby, dict) and emby.get("api_key"):
        emby["api_key"] = REDACTION_PLACEHOLDER

    network = out.get("network")
    if isinstance(network, dict) and network.get("proxy"):
        network["proxy"] = redact_proxy(str(network["proxy"]))

    r18 = out.get("r18")
    if isinstance(r18, dict):
        if r18.get("read_password"):
            r18["read_password"] = REDACTION_PLACEHOLDER
        if r18.get("dsn"):
            r18["dsn"] = redact_dsn(str(r18["dsn"]))

    plugins = out.get("plugins")
    if isinstance(plugins, dict):
        for plugin in plugins.values():
            if isinstance(plugin, dict):
                plugin["config"] = _redact_secret_values(plugin.get("config"))

    return out


_SECRET_KEY_PARTS = ("api_key", "apikey", "token", "secret", "password", "cookie", "credential", "dsn")


def _is_secret_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _has_secret_value(value: object, *, key: object | None = None) -> bool:
    if key is not None and _is_secret_key(key):
        return bool(value)
    if isinstance(value, dict):
        return any(_has_secret_value(v, key=k) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_secret_value(v) for v in value)
    return False


def _redact_secret_values(value: object, *, key: object | None = None) -> object:
    if key is not None and _is_secret_key(key):
        return REDACTION_PLACEHOLDER if value else value
    if isinstance(value, dict):
        return {key: _redact_secret_values(item, key=key) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_secret_values(item) for item in value]
    return value


def redact_proxy(proxy: str) -> str:
    """剥离 proxy URL 中的 userinfo, 保留 scheme/host/port/path."""
    parts = urlsplit(proxy)
    host = parts.hostname
    if host is None:
        return proxy
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def redact_dsn(dsn: str) -> str:
    try:
        return make_url(dsn).render_as_string(hide_password=True)
    except Exception:
        return REDACTION_PLACEHOLDER

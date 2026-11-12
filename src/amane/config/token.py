"""未设置时生成随机 token 并持久化到 ``data_dir`` 下的文件 (0600), 后续启动复用.
``off`` 显式关闭校验. 文件已存在时直接读取, 不会覆盖.
"""

from __future__ import annotations

import secrets
from pathlib import Path

_API_TOKEN_FILE = "token"
_IMAGE_TOKEN_FILE = "image_token"


def resolve_api_token(token_env: str | None, data_dir: Path) -> str | None:
    """``off`` 时返回 ``None``."""
    return _resolve(token_env, data_dir / _API_TOKEN_FILE)


def resolve_image_token(token_env: str | None, data_dir: Path) -> str | None:
    """``off`` 时返回 ``None``; 独立于 API token, 只用于资源端点."""
    return _resolve(token_env, data_dir / _IMAGE_TOKEN_FILE)


def _resolve(token_env: str | None, token_path: Path) -> str | None:
    if token_env == "off":
        return None
    if token_env:
        return token_env

    # 已有文件则复用, 不覆盖
    if token_path.is_file():
        value = token_path.read_text(encoding="utf-8").strip()
        return value or None

    # 生成并持久化 (0600)
    token = secrets.token_urlsafe(32)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token, encoding="utf-8")
    token_path.chmod(0o600)
    return token

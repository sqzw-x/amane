"""缓存在 ``{data_dir}/tools/{tool}/``. 首次 ensure_binary() 时从 GitHub Release 下载."""

import os
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

import httpx2 as httpx
import structlog

from .tool import SrTool, get_tool_meta

logger = structlog.get_logger()


def get_tool_dir(data_dir: Path) -> Path:
    return data_dir.absolute() / "tools"


def get_binary_path(tool: SrTool, data_dir: Path) -> Path:
    meta = get_tool_meta(tool)
    name = meta.binary_name
    # Windows 官方包是 .exe; CreateProcess 在路径含目录时不会补后缀.
    if sys.platform == "win32":
        name = f"{name}.exe"
    return get_tool_dir(data_dir) / tool / name


def is_binary_available(tool: SrTool, data_dir: Path) -> bool:
    binary_path = get_binary_path(tool, data_dir)
    if not binary_path.is_file():
        return False
    # Windows 无 POSIX 执行位; 文件存在即视作就绪.
    if sys.platform == "win32":
        return True
    return os.access(binary_path, os.X_OK)


async def ensure_binary(tool: SrTool, data_dir: Path, client: httpx.AsyncClient | None = None) -> Path:
    """已缓存则直接返回, 否则下载."""

    binary_path = get_binary_path(tool, data_dir)
    if is_binary_available(tool, data_dir):
        logger.debug("sr binary exists", path=str(binary_path))
        return binary_path

    logger.info("sr binary not found, downloading", tool=tool)
    owned_client = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(600), follow_redirects=True)

    try:
        await _download_binary(tool, binary_path.parent, client)
    finally:
        if owned_client:
            await client.aclose()

    # 设置可执行位
    _make_executable(binary_path)

    logger.info("sr binary ready", path=str(binary_path))
    return binary_path


async def _download_binary(tool: SrTool, dest_dir: Path, client: httpx.AsyncClient) -> None:
    meta = get_tool_meta(tool)
    download_url = meta.download_urls.get(sys.platform)
    if download_url is None:
        raise RuntimeError(f"不支持的操作系统: {sys.platform}")

    # 下载到临时文件
    logger.info("downloading sr binary", url=download_url)
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        try:
            async with client.stream("GET", download_url) as stream:
                stream.raise_for_status()
                async for chunk in stream.aiter_bytes(chunk_size=8192):
                    tmp.write(chunk)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    # 解压
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)

        # ncnn-vulkan 的 zip 通常有一层顶层目录: 解压到父目录后整体改名到 dest_dir.
        with zipfile.ZipFile(tmp_path) as zf:
            names = zf.namelist()
            prefix = _find_common_prefix(names)

            if prefix and prefix != "./":
                extract_to = dest_dir.parent
                zf.extractall(extract_to)
                extracted_dir = extract_to / prefix.rstrip("/")
                if extracted_dir.is_dir() and extracted_dir != dest_dir:
                    if dest_dir.exists():
                        shutil.rmtree(dest_dir)
                    extracted_dir.rename(dest_dir)
            else:
                zf.extractall(dest_dir)

        logger.info("sr binary extracted", dest=str(dest_dir))
    finally:
        tmp_path.unlink(missing_ok=True)


def _find_common_prefix(names: list[str]) -> str | None:
    if not names:
        return None

    first = names[0]
    if "/" not in first:
        return None

    prefix = first.split("/")[0] + "/"

    if all(n.startswith(prefix) for n in names):
        return prefix

    return None


def _make_executable(path: Path) -> None:
    current = path.stat().st_mode
    path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

"""托管模型的清单与下载.

模型文件放在 ``{data_dir}/models/{name}/{version}``, 只由索引任务补齐 (检索只查本地); 下载走 HuggingFace 直链,
断点续传 + 重试 + SHA256 校验. Core ML 的 ``.mlpackage`` 以 zip 分发, 校验后解压并删掉压缩包.

清单是代码常量: 换模型 = 加一条清单并改 ``DEFAULT_SPEC``, 不扫描目录猜测.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import aiofiles
import httpx2 as httpx
import structlog

from .encoder import SemanticUnavailable, artifact_present

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = structlog.get_logger()

BASE_URL = "https://huggingface.co/sqzwx/amane-siglip2-base/resolve/v1"

Backend = Literal["coreml", "onnx"]

RETRIES = 4
"""单个文件的下载尝试次数; 每次失败前会删掉残缺文件, 避免按错误偏移续传."""

CHUNK = 1 << 20

PROGRESS_STEP = 64 << 20
"""下载进度日志的间隔 (字节)."""


@dataclass(frozen=True)
class RemoteFile:
    path: str
    """仓库内路径, 同时拼出下载地址."""
    local: str
    """准备完成后的本地相对路径."""
    sha256: str
    size: int
    """分发文件的大小; 压缩包为压缩后的体积."""
    installed_size: int | None = None
    """解压后的本地大小; 非压缩文件省略, 等于 ``size``."""
    archive: bool = False


@dataclass(frozen=True)
class ModelSpec:
    name: str
    display_name: str
    """界面展示名, 大小写按厂商写法; ``name`` 只作标识与目录名."""
    version: str
    dim: int
    tokenizer: RemoteFile
    backends: dict[Backend, tuple[RemoteFile, RemoteFile]]
    """``backend → (视觉塔, 文本塔)``; 显式列出每种后端的分发精度."""

    def files(self, backend: Backend) -> Iterator[RemoteFile]:
        yield self.tokenizer
        vision, text = self.backends[backend]
        yield vision
        yield text


@dataclass(frozen=True)
class ModelPaths:
    spec: ModelSpec
    backend: Backend
    tokenizer: Path
    vision: Path
    text: Path


_SIGLIP2_BASE_V1 = ModelSpec(
    name="siglip2-base",
    display_name="SigLIP2 Base",
    version="v1",
    dim=768,
    tokenizer=RemoteFile(
        path="tokenizer.json",
        local="tokenizer.json",
        sha256="cb9140fae3ac5122c972d37adf83e1248471a38147ad76f8215c8872c6fd8322",
        size=34_363_039,
    ),
    backends={
        "coreml": (
            RemoteFile(
                path="coreml/siglip2-base-v-b1-fp16.zip",
                local="coreml/siglip2-base-v-b1-fp16.mlpackage",
                sha256="25739557222f2a68f3d416602460bbd6d7c61580df9ad0bc3c0cbfcca60fbfe9",
                size=170_629_287,
                installed_size=184_962_436,
                archive=True,
            ),
            RemoteFile(
                path="coreml/siglip2-base-text-b1-fp16-p8bit.zip",
                local="coreml/siglip2-base-text-b1-fp16-p8bit.mlpackage",
                sha256="83ea4087caef743990cb2ef47fb766d30e3ded6415b5500096449826e627f8f8",
                size=258_357_347,
                installed_size=282_619_673,
                archive=True,
            ),
        ),
        "onnx": (
            RemoteFile(
                path="onnx/vision_model_fp16.onnx",
                local="onnx/vision_model_fp16.onnx",
                sha256="fe9ad8020a6d3d98d394c9be8f07064066135fc2f87ec11692de0b677c0ac4db",
                size=186_131_676,
            ),
            RemoteFile(
                path="onnx/text_model_fp16.onnx",
                local="onnx/text_model_fp16.onnx",
                sha256="80954edffdc689599e5d5bc6a1738380bc9e8139a18e5c8892485f248b6b4890",
                size=564_862_230,
            ),
        ),
    },
)

SPECS: dict[tuple[str, str], ModelSpec] = {(_SIGLIP2_BASE_V1.name, _SIGLIP2_BASE_V1.version): _SIGLIP2_BASE_V1}

DEFAULT_SPEC = _SIGLIP2_BASE_V1
"""服务固定使用的模型; 多模型时由状态接口列出可选清单, 不交给用户填配置."""


def spec_for(name: str, version: str) -> ModelSpec | None:
    return SPECS.get((name, version))


def backend_for_platform() -> Backend:
    """macOS 用 Core ML (ANE), 其余平台用 ONNX Runtime."""
    return "coreml" if sys.platform == "darwin" else "onnx"


BACKEND_LABELS: dict[Backend, str] = {"coreml": "Core ML", "onnx": "ONNX Runtime"}
"""界面展示名; 枚举值只用于标识、配置与存储."""


def backend_label(backend: Backend) -> str:
    return BACKEND_LABELS[backend]


def model_key(name: str, version: str) -> str:
    return f"{name}-{version}"


def model_root(data_dir: Path, spec: ModelSpec) -> Path:
    return data_dir / "models" / spec.name / spec.version


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _extract(archive: Path, dest: Path) -> Path:
    """解压单顶层目录的 zip; 返回顶层目录. 路径穿越直接拒绝."""
    with zipfile.ZipFile(archive) as bundle:
        names = [name for name in bundle.namelist() if name.strip()]
        for name in names:
            parts = Path(name).parts
            if Path(name).is_absolute() or ".." in parts:
                raise SemanticUnavailable(f"模型压缩包包含非法路径: {name}")
        tops = {Path(name).parts[0] for name in names}
        bundle.extractall(dest)
    if len(tops) != 1:
        raise SemanticUnavailable(f"模型压缩包应只有一个顶层目录: {archive.name}")
    return dest / tops.pop()


async def _fetch(client: httpx.AsyncClient, url: str, part: Path) -> None:
    """把 ``url`` 续写进 ``part``; 服务器不支持 Range 时从头重写."""
    offset = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    async with client.stream("GET", url, headers=headers) as response:
        if offset and response.status_code == httpx.codes.OK:
            offset = 0
        elif offset and response.status_code == httpx.codes.REQUESTED_RANGE_NOT_SATISFIABLE:
            return
        response.raise_for_status()
        received = offset
        next_log = offset + PROGRESS_STEP
        async with aiofiles.open(part, "ab" if offset else "wb") as handle:
            async for chunk in response.aiter_bytes(CHUNK):
                await handle.write(chunk)
                received += len(chunk)
                if received >= next_log:
                    logger.info("model download progress", url=url, received_mb=round(received / 1e6))
                    next_log += PROGRESS_STEP


async def _download(client: httpx.AsyncClient, url: str, target: Path, remote: RemoteFile) -> None:
    part = target.with_name(f"{target.name}.part")
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, RETRIES + 1):
        try:
            await _fetch(client, url, part)
            size = part.stat().st_size if part.exists() else 0
            if size != remote.size:
                raise OSError(f"大小不符: {size} != {remote.size}")
            digest = await asyncio.to_thread(_sha256, part)
            if digest != remote.sha256:
                raise OSError(f"SHA256 不符: {digest}")
            part.replace(target)
            return
        except Exception as exc:
            part.unlink(missing_ok=True)
            if attempt == RETRIES:
                raise SemanticUnavailable(f"模型文件下载失败: {remote.path}: {exc}") from exc
            logger.warning("model download retry", file=remote.path, attempt=attempt, error=str(exc))
            await asyncio.sleep(min(2**attempt, 30))


async def _prepare(client: httpx.AsyncClient, root: Path, remote: RemoteFile) -> Path:
    target = root / remote.local
    if target.exists():
        return target
    url = f"{BASE_URL}/{remote.path}"
    archive = root / remote.path
    await _download(client, url, archive, remote)
    if not remote.archive:
        return archive
    extracted = await asyncio.to_thread(_extract, archive, archive.parent)
    if extracted != target:
        raise SemanticUnavailable(f"模型压缩包目录名不符: {extracted.name} != {target.name}")
    archive.unlink()
    return target


def model_paths(spec: ModelSpec, backend: Backend, data_dir: Path) -> ModelPaths:
    root = model_root(data_dir, spec)
    return ModelPaths(
        spec=spec,
        backend=backend,
        tokenizer=root / spec.tokenizer.local,
        vision=root / spec.backends[backend][0].local,
        text=root / spec.backends[backend][1].local,
    )


def model_ready(spec: ModelSpec, backend: Backend, data_dir: Path) -> bool:
    """就绪标记与三个文件 (或 Core ML 编译产物) 都在; 只查文件系统, 不联网."""
    paths = model_paths(spec, backend, data_dir)
    ready = model_root(data_dir, spec) / f"{backend}.ready"
    return ready.exists() and all(artifact_present(path) for path in (paths.tokenizer, paths.vision, paths.text))


@dataclass(frozen=True)
class Readiness:
    """两个塔各自是否就绪: 检索只用文本塔与 tokenizer, 索引只用视觉塔."""

    search: bool
    index: bool


def download_size(spec: ModelSpec, backend: Backend) -> int:
    """当前后端首次下载的总字节数 (压缩包大小, 不含解压后占用)."""
    return sum(remote.size for remote in spec.files(backend))


def tower_sizes(spec: ModelSpec, backend: Backend) -> tuple[int, int]:
    """视觉塔与文本塔各自解压后的本地大小; tokenizer 不计入任一塔."""
    vision, text = spec.backends[backend]
    return (
        vision.size if vision.installed_size is None else vision.installed_size,
        text.size if text.installed_size is None else text.installed_size,
    )


def readiness(spec: ModelSpec, backend: Backend, data_dir: Path) -> Readiness:
    """按塔分别判断本地文件, 不看就绪标记: 文件在下载完成前不会以最终名字出现."""
    paths = model_paths(spec, backend, data_dir)
    return Readiness(
        search=paths.tokenizer.exists() and artifact_present(paths.text),
        index=artifact_present(paths.vision),
    )


async def ensure_model_files(spec: ModelSpec, backend: Backend, data_dir: Path) -> ModelPaths:
    """按需下载缺失文件并返回三个路径; 已就绪时只做存在性检查."""
    paths = model_paths(spec, backend, data_dir)
    if model_ready(spec, backend, data_dir):
        return paths

    root = model_root(data_dir, spec)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SemanticUnavailable(f"无法创建模型目录: {root}: {exc}") from exc
    logger.info("model files incomplete, downloading", model=spec.name, version=spec.version, backend=backend)
    timeout = httpx.Timeout(30.0, read=120.0)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        for remote in spec.files(backend):
            await _prepare(client, root, remote)
    (root / f"{backend}.ready").write_text(f"{spec.name} {spec.version} {backend}\n", encoding="utf-8")
    logger.info("model files ready", model=spec.name, version=spec.version, backend=backend, root=str(root))
    return paths

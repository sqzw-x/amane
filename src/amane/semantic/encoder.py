from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import numpy as np
from PIL import Image
from tokenizers import Tokenizer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from .models import ModelPaths

IMAGE_SIZE = 256
"""SigLIP2 base 的固定输入边长; 官方预处理直接拉伸, 不保持比例."""

TEXT_LENGTH = 64
"""文本塔的固定序列长度 (tokenizer 的 padding 长度)."""

PAD_TOKEN = "<pad>"


class SemanticUnavailable(RuntimeError):
    """模型文件缺失或后端不可用; 消息直接展示给用户."""


class ImageEncoder(Protocol):
    """视觉塔的进程内封装; 输出 L2 归一化的 float32. 实例不保证线程安全, 调用方串行化."""

    dim: int

    def embed_images(self, images: Sequence[Image.Image]) -> np.ndarray: ...


class TextEncoder(Protocol):
    """文本塔 + tokenizer 的进程内封装; 输出 L2 归一化的 float32. 实例不保证线程安全, 调用方串行化."""

    dim: int

    def embed_text(self, text: str) -> np.ndarray: ...


def preprocess_image(image: Image.Image) -> np.ndarray:
    """官方 SiglipImageProcessor: resize 到 256×256 (拉伸), 归一化 (x/255-0.5)/0.5."""
    resized = image.convert("RGB").resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.BILINEAR)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    array = (array - 0.5) / 0.5
    return np.transpose(array, (2, 0, 1))


def encode_text(tokenizer: Tokenizer, text: str) -> np.ndarray:
    """截断 / 补齐到固定长度; 与训练时的 padding=max_length 一致."""
    ids = list(tokenizer.encode(text).ids[:TEXT_LENGTH])
    pad_id = tokenizer.token_to_id(PAD_TOKEN)
    ids.extend([0 if pad_id is None else pad_id] * (TEXT_LENGTH - len(ids)))
    return np.asarray([ids], dtype=np.int64)


def normalize(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix[None, :]
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def load_tokenizer(path: Path) -> Tokenizer:
    if not path.is_file():
        raise SemanticUnavailable(f"tokenizer 文件不存在: {path}")
    return Tokenizer.from_file(str(path))


def artifact_present(path: Path) -> bool:
    """模型文件或其编译产物存在; Core ML 编译后可删除原包以减少磁盘占用."""
    if path.exists():
        return True
    if path.suffix == ".mlpackage":
        return (path.parent / "compiled" / f"{path.stem}.mlmodelc").is_dir()
    return False


def _require(label: str, path: Path) -> None:
    if not artifact_present(path):
        raise SemanticUnavailable(f"{label}文件不存在: {path}")


def build_image_encoder(paths: ModelPaths) -> ImageEncoder:
    """加载视觉塔; 阻塞调用, 调用方负责在线程中执行."""
    _require("视觉模型", paths.vision)
    if paths.backend == "coreml":
        # 可选的重型依赖: 仅在真正使用时导入, 避免延长普通启动时间.
        from .coreml import CoreMlImageEncoder

        return CoreMlImageEncoder(paths.vision, paths.spec.dim)
    from .onnx import OnnxImageEncoder

    return OnnxImageEncoder(paths.vision, paths.spec.dim)


def build_text_encoder(paths: ModelPaths) -> TextEncoder:
    """加载文本塔与 tokenizer; 阻塞调用, 调用方负责在线程中执行."""
    _require("文本模型", paths.text)
    if paths.backend == "coreml":
        from .coreml import CoreMlTextEncoder

        return CoreMlTextEncoder(paths.text, paths.tokenizer, paths.spec.dim)
    from .onnx import OnnxTextEncoder

    return OnnxTextEncoder(paths.text, paths.tokenizer, paths.spec.dim)

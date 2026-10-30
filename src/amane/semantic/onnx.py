"""ONNX Runtime 后端 (全平台): 视觉塔吃 float32 像素, 文本塔吃 int64 token id."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import onnxruntime as ort

from .encoder import encode_text, load_tokenizer, normalize, preprocess_image

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from PIL import Image

_VISION_INPUT = "pixel_values"
_TEXT_INPUT = "input_ids"
_OUTPUT = "pooler_output"


def _session(path: Path, input_name: str) -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
    if input_name not in {item.name for item in session.get_inputs()}:
        raise RuntimeError(f"ONNX 模型缺少输入 {input_name}: {path}")
    return session


class OnnxImageEncoder:
    def __init__(self, path: Path, dim: int) -> None:
        self._session = _session(path, _VISION_INPUT)
        self.dim = dim

    def embed_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        batch = np.stack([preprocess_image(image) for image in images]).astype(np.float32)
        output = np.asarray(self._session.run([_OUTPUT], {_VISION_INPUT: batch})[0], dtype=np.float32)
        return normalize(output)


class OnnxTextEncoder:
    def __init__(self, path: Path, tokenizer_path: Path, dim: int) -> None:
        self._tokenizer = load_tokenizer(tokenizer_path)
        self._session = _session(path, _TEXT_INPUT)
        self.dim = dim

    def embed_text(self, text: str) -> np.ndarray:
        ids = encode_text(self._tokenizer, text)
        output = np.asarray(self._session.run([_OUTPUT], {_TEXT_INPUT: ids})[0], dtype=np.float32)
        return normalize(output)[0]

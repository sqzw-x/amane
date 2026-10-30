"""Core ML 后端 (macOS): 直接调系统 Core ML 框架, 不依赖 coremltools 运行时.

模型可以是 ``.mlpackage`` 或已编译的 ``.mlmodelc``; 前者会编译到模型同级的 ``compiled/``
目录并复用, 否则每次启动都要重做 ANE 特化 (实测 8–15 s). 计算单元先试 ANE, 失败回退 CPU.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from CoreML import MLDictionaryFeatureProvider, MLModel, MLModelConfiguration, MLMultiArray  # ty: ignore[unresolved-import]
from Foundation import NSURL  # ty: ignore[unresolved-import]

from .encoder import encode_text, load_tokenizer, normalize, preprocess_image

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PIL import Image

FLOAT16 = 65552
FLOAT32 = 65568
INT32 = 131104

_NUMPY_DTYPE = {FLOAT16: np.float16, FLOAT32: np.float32, INT32: np.int32}

CPU_AND_NE = 3
CPU_ONLY = 0

_VISION_INPUT = "pixel_values"
_TEXT_INPUT = "input_ids"
_OUTPUT = "pooler_output"


def _compile(path: Path) -> Path:
    if path.suffix != ".mlpackage":
        return path
    target = path.parent / "compiled" / f"{path.stem}.mlmodelc"
    if target.is_dir():
        return target
    url, error = MLModel.compileModelAtURL_error_(NSURL.fileURLWithPath_(str(path)), None)
    if url is None:
        raise RuntimeError(f"Core ML 模型编译失败: {path}: {error}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(url.path()), str(target), dirs_exist_ok=True)
    return target


def _load(path: Path) -> MLModel:
    compiled = _compile(path)
    last_error: object = None
    for units in (CPU_AND_NE, CPU_ONLY):
        config = MLModelConfiguration.alloc().init()
        config.setComputeUnits_(units)
        model, error = MLModel.modelWithContentsOfURL_configuration_error_(
            NSURL.fileURLWithPath_(str(compiled)), config, None
        )
        if model is not None:
            return model
        last_error = error
    raise RuntimeError(f"Core ML 模型加载失败: {path}: {last_error}")


def _dtype(model: MLModel, name: str) -> int:
    description = model.modelDescription().inputDescriptionsByName().get(name)
    if description is None:
        raise RuntimeError(f"模型缺少输入 {name}")
    return int(description.multiArrayConstraint().dataType())


def _to_array(values: np.ndarray, dtype: int) -> MLMultiArray:
    data = np.ascontiguousarray(values, dtype=_NUMPY_DTYPE[dtype])
    array, error = MLMultiArray.alloc().initWithShape_dataType_error_(list(data.shape), dtype, None)
    if array is None:
        raise RuntimeError(f"MLMultiArray 创建失败: {error}")
    array.dataPointer().as_buffer(data.nbytes)[:] = data.tobytes()
    return array


def _from_array(array: MLMultiArray) -> np.ndarray:
    dtype = _NUMPY_DTYPE[int(array.dataType())]
    buffer = array.dataPointer().as_buffer(array.count() * np.dtype(dtype).itemsize)
    return np.frombuffer(buffer, dtype=dtype).astype(np.float32).reshape(array.shape())


def _predict(model: MLModel, input_name: str, values: np.ndarray, dtype: int) -> np.ndarray:
    provider, error = MLDictionaryFeatureProvider.alloc().initWithDictionary_error_(
        {input_name: _to_array(values, dtype)}, None
    )
    if provider is None:
        raise RuntimeError(f"Core ML 输入构造失败: {error}")
    output, error = model.predictionFromFeatures_error_(provider, None)
    if output is None:
        raise RuntimeError(f"Core ML 推理失败: {error}")
    value = output.featureValueForName_(_OUTPUT).multiArrayValue()
    if value is None:
        raise RuntimeError(f"Core ML 输出缺少 {_OUTPUT}")
    return _from_array(value)


class CoreMlImageEncoder:
    def __init__(self, path: Path, dim: int) -> None:
        self._model = _load(path)
        self._dtype = _dtype(self._model, _VISION_INPUT)
        self.dim = dim

    def embed_images(self, images: Sequence[Image.Image]) -> np.ndarray:
        # 视觉塔是 batch=1 包 (ANE 上 batch>1 会静默出错), 逐张推理.
        outputs = [
            _predict(self._model, _VISION_INPUT, preprocess_image(image)[None, ...], self._dtype) for image in images
        ]
        return normalize(np.concatenate(outputs))


class CoreMlTextEncoder:
    def __init__(self, path: Path, tokenizer_path: Path, dim: int) -> None:
        self._tokenizer = load_tokenizer(tokenizer_path)
        self._model = _load(path)
        self._dtype = _dtype(self._model, _TEXT_INPUT)
        self.dim = dim

    def embed_text(self, text: str) -> np.ndarray:
        ids = encode_text(self._tokenizer, text)
        return normalize(_predict(self._model, _TEXT_INPUT, ids, self._dtype))[0]

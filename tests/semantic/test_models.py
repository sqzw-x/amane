"""模型清单与下载: 校验、重试、压缩包解压与就绪标记 (不联网)."""

import hashlib
import zipfile
from pathlib import Path

import pytest

from amane.semantic import SemanticUnavailable
from amane.semantic import models as registry


def _remote(tmp_path: Path, payload: bytes, *, archive: bool = False) -> tuple[registry.RemoteFile, Path]:
    source = tmp_path / ("bundle.zip" if archive else "weights.bin")
    source.write_bytes(payload)
    return (
        registry.RemoteFile(
            path=source.name,
            local="model.mlpackage" if archive else "weights.bin",
            sha256=hashlib.sha256(payload).hexdigest(),
            size=len(payload),
            archive=archive,
        ),
        source,
    )


@pytest.mark.asyncio
async def test_download_verifies_hash_and_retries_on_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote, source = _remote(tmp_path, b"correct")
    attempts = 0
    sleeps: list[float] = []

    async def fake_fetch(client: object, url: str, part: Path) -> None:
        nonlocal attempts
        attempts += 1
        part.write_bytes(b"wrong" if attempts < 3 else source.read_bytes())

    async def no_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(registry, "_fetch", fake_fetch)
    monkeypatch.setattr(registry.asyncio, "sleep", no_sleep)
    target = tmp_path / "out" / "weights.bin"
    await registry._download(None, "http://example.com/x", target, remote)  # type: ignore[arg-type]

    assert attempts == 3
    assert target.read_bytes() == b"correct"
    assert not target.with_name("weights.bin.part").exists()
    assert len(sleeps) == 2


@pytest.mark.asyncio
async def test_download_gives_up_after_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote, _ = _remote(tmp_path, b"correct")

    async def fake_fetch(client: object, url: str, part: Path) -> None:
        part.write_bytes(b"wrong")

    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(registry, "_fetch", fake_fetch)
    monkeypatch.setattr(registry.asyncio, "sleep", no_sleep)
    target = tmp_path / "dest" / "weights.bin"
    with pytest.raises(SemanticUnavailable, match="下载失败"):
        await registry._download(None, "http://example.com/x", target, remote)  # type: ignore[arg-type]
    assert not target.exists()
    assert not target.with_name("weights.bin.part").exists()


def test_extract_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../evil.mlpackage/Manifest.json", "{}")
    with pytest.raises(SemanticUnavailable, match="非法路径"):
        registry._extract(archive, tmp_path)


def test_extract_requires_single_top_level_dir(tmp_path: Path) -> None:
    archive = tmp_path / "two.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("a/Manifest.json", "{}")
        bundle.writestr("b/Manifest.json", "{}")
    with pytest.raises(SemanticUnavailable, match="顶层目录"):
        registry._extract(archive, tmp_path)


@pytest.mark.asyncio
async def test_ensure_model_files_skips_when_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = registry.spec_for("siglip2-base", "v1")
    assert spec is not None
    backend: registry.Backend = "onnx"
    root = registry.model_root(tmp_path, spec)
    for remote in spec.files(backend):
        target = root / remote.local
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    (root / f"{backend}.ready").write_text("ready", encoding="utf-8")

    async def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("就绪后不应再下载")

    monkeypatch.setattr(registry, "_prepare", explode)
    paths = await registry.ensure_model_files(spec, backend, tmp_path)
    assert (paths.vision.name, paths.text.suffix, paths.tokenizer.name) == (
        "vision_model_fp16.onnx",
        ".onnx",
        "tokenizer.json",
    )


@pytest.mark.asyncio
async def test_ensure_model_files_prepares_archives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = registry.RemoteFile(
        path="coreml/vision.zip",
        local="coreml/vision.mlpackage",
        sha256="",
        size=0,
        archive=True,
    )
    tokenizer = registry.RemoteFile(path="tokenizer.json", local="tokenizer.json", sha256="", size=0)
    spec = registry.ModelSpec(
        name="test-model",
        display_name="Test Model",
        version="v1",
        dim=4,
        tokenizer=tokenizer,
        backends={"coreml": (archive, archive), "onnx": (archive, archive)},
    )

    async def fake_download(client: object, url: str, target: Path, item: registry.RemoteFile) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if item.archive:
            with zipfile.ZipFile(target, "w") as bundle:
                bundle.writestr(f"{Path(item.local).name}/Manifest.json", "{}")
        else:
            target.write_bytes(b"tok")

    monkeypatch.setattr(registry, "_download", fake_download)
    paths = await registry.ensure_model_files(spec, "coreml", tmp_path)

    assert paths.vision == tmp_path / "models" / "test-model" / "v1" / "coreml" / "vision.mlpackage"
    assert (paths.vision / "Manifest.json").is_file()
    assert paths.tokenizer.read_bytes() == b"tok"
    assert (paths.vision.parent / "vision.zip").exists() is False


def test_readiness_accepts_compiled_coreml(tmp_path: Path) -> None:
    """Core ML 编译产物在时, 原 mlpackage 可以删掉, 两塔仍视为就绪."""
    spec = registry.DEFAULT_SPEC
    backend: registry.Backend = "coreml"
    paths = registry.model_paths(spec, backend, tmp_path)
    paths.tokenizer.parent.mkdir(parents=True, exist_ok=True)
    paths.tokenizer.write_text("{}", encoding="utf-8")
    for model in (paths.vision, paths.text):
        compiled = model.parent / "compiled" / f"{model.stem}.mlmodelc"
        compiled.mkdir(parents=True)
        (compiled / "coremldata.bin").write_bytes(b"x")
    (registry.model_root(tmp_path, spec) / f"{backend}.ready").write_text("ready", encoding="utf-8")

    assert registry.readiness(spec, backend, tmp_path) == registry.Readiness(search=True, index=True)
    assert registry.model_ready(spec, backend, tmp_path)
    assert registry.artifact_present(paths.text)
    assert not registry.artifact_present(paths.vision.parent / "missing.mlpackage")


@pytest.mark.parametrize(
    ("backend", "compressed"),
    [("coreml", True), ("onnx", False)],
)
def test_tower_sizes_are_uncompressed(backend: registry.Backend, *, compressed: bool) -> None:
    """压缩包分发的后端必须上报解压后的体积, 不能与下载体积相同."""
    vision, text = registry.DEFAULT_SPEC.backends[backend]
    vision_size, text_size = registry.tower_sizes(registry.DEFAULT_SPEC, backend)
    assert (vision_size > vision.size) is compressed
    assert (text_size > text.size) is compressed

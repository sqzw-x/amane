"""run_SR() 集成测试 - 使用真实二进制."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from amane.config import SrConfig
from amane.sr import SrPreset, SrTool, get_binary_path, run_SR

_PROJ_ROOT = Path(__file__).resolve().parents[2]


def _find_binary(tool: SrTool) -> Path | None:
    """查找应用已缓存的二进制, 找不到返回 None."""
    p = get_binary_path(tool, _PROJ_ROOT / "data")
    if p.is_file():
        return p
    return None


@pytest.fixture
def test_image(tmp_path: Path) -> Path:
    """生成测试用 JPEG."""
    from PIL import Image

    dst = tmp_path / "input.jpg"
    img = Image.new("RGB", (32, 32), color=(30, 80, 200))
    img.save(dst, "JPEG")
    return dst


def _mock_ensure_binary(tool: SrTool):
    """mock 下载, 直接用本地二进制."""
    binary = _find_binary(tool)

    async def mock(tool: SrTool, data_dir: Path, client=None) -> Path:
        if binary is None or not binary.is_file():
            raise FileNotFoundError(f"二进制不存在且无法下载: {tool}")
        return binary

    return AsyncMock(side_effect=mock)


def _require_binary(tool: SrTool):
    """如果没有二进制则跳过测试."""
    if _find_binary(tool) is None:
        pytest.skip(f"{tool} 二进制未找到 (SR/ 目录)")


@pytest.mark.asyncio
class TestRunSuccess:
    """正常执行."""

    async def test_waifu2x_file_to_file(self, test_image: Path, tmp_path: Path):
        _require_binary(SrTool.WAIFU2X)
        cfg = SrConfig(enabled=True, preset=SrPreset.WAIFU_PHOTO_2X, output_format="jpg")
        output = tmp_path / "out.jpg"

        with patch("amane.sr.run.ensure_binary", _mock_ensure_binary(SrTool.WAIFU2X)):
            result = await run_SR(test_image, output, cfg, tmp_path)

        assert result.success
        assert output.is_file()
        assert result.duration_ms > 0
        assert result.input_size > 0
        assert result.output_size > 0

    async def test_realesrgan_file_to_file(self, test_image: Path, tmp_path: Path):
        _require_binary(SrTool.REALESRGAN)
        cfg = SrConfig(enabled=True, preset=SrPreset.REALESR_PHOTO_4X, output_format="jpg")
        output = tmp_path / "out.jpg"

        with patch("amane.sr.run.ensure_binary", _mock_ensure_binary(SrTool.REALESRGAN)):
            result = await run_SR(test_image, output, cfg, tmp_path)

        assert result.success

    async def test_dir_to_dir(self, test_image: Path, tmp_path: Path):
        _require_binary(SrTool.WAIFU2X)
        input_dir = tmp_path / "in"
        input_dir.mkdir()
        (input_dir / "img.jpg").write_bytes(test_image.read_bytes())
        output_dir = tmp_path / "out"

        cfg = SrConfig(enabled=True, preset=SrPreset.WAIFU_PHOTO_2X)
        with patch("amane.sr.run.ensure_binary", _mock_ensure_binary(SrTool.WAIFU2X)):
            result = await run_SR(input_dir, output_dir, cfg, tmp_path)

        assert result.success
        assert len(list(output_dir.iterdir())) >= 1


@pytest.mark.asyncio
class TestRunErrors:
    """异常处理."""

    async def test_missing_input(self, tmp_path: Path):
        _require_binary(SrTool.WAIFU2X)
        cfg = SrConfig(enabled=True, preset=SrPreset.WAIFU_PHOTO_2X)
        output = tmp_path / "out.jpg"

        with patch("amane.sr.run.ensure_binary", _mock_ensure_binary(SrTool.WAIFU2X)):
            result = await run_SR(tmp_path / "nonexistent.jpg", output, cfg, tmp_path)

        assert not result.success
        assert result.error is not None

    async def test_output_dir_auto_created(self, test_image: Path, tmp_path: Path):
        _require_binary(SrTool.WAIFU2X)
        cfg = SrConfig(enabled=True, preset=SrPreset.WAIFU_PHOTO_2X, output_format="jpg")
        output = tmp_path / "subdir" / "out.jpg"
        assert not output.parent.exists()

        with patch("amane.sr.run.ensure_binary", _mock_ensure_binary(SrTool.WAIFU2X)):
            result = await run_SR(test_image, output, cfg, tmp_path)

        assert result.success
        assert output.is_file()

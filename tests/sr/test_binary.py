"""binary 模块测试 - 平台检测, 路径生成, 可用性检查, 下载 URL."""

import stat
import sys
from pathlib import Path

import pytest

from amane.sr import SrTool, get_binary_path, get_tool_dir, is_binary_available
from amane.sr.tool import get_tool_meta


class TestDownloadUrls:
    """下载 URL dict."""

    def test_realesrgan_urls(self):
        """realesrgan 三平台 URL 已锁定 (含 models 的完整包)."""
        meta = get_tool_meta(SrTool.REALESRGAN)
        assert set(meta.download_urls.keys()) == {"darwin", "linux", "win32"}
        assert meta.download_urls["darwin"].endswith("realesrgan-ncnn-vulkan-20220424-macos.zip")
        assert "xinntao/Real-ESRGAN" in meta.download_urls["darwin"]

    def test_waifu2x_urls(self):
        """waifu2x 三平台 URL 已锁定."""
        meta = get_tool_meta(SrTool.WAIFU2X)
        assert set(meta.download_urls.keys()) == {"darwin", "linux", "win32"}
        assert meta.download_urls["linux"].endswith("waifu2x-ncnn-vulkan-20250915-linux.zip")
        assert "nihui/waifu2x-ncnn-vulkan" in meta.download_urls["linux"]


class TestGetToolDir:
    """二进制缓存目录."""

    def test_returns_tools_under_data_dir(self):
        result = get_tool_dir(Path("/data"))
        assert result == Path("/data/tools")


class TestGetBinaryPath:
    """二进制路径生成."""

    def test_realesrgan_path(self):
        path = get_binary_path(SrTool.REALESRGAN, Path("/data"))
        assert path == Path("/data/tools/realesrgan/realesrgan-ncnn-vulkan")

    def test_waifu2x_path(self):
        path = get_binary_path(SrTool.WAIFU2X, Path("/data"))
        assert path == Path("/data/tools/waifu2x/waifu2x-ncnn-vulkan")


class TestIsBinaryAvailable:
    """二进制可用性检查."""

    def test_missing_binary(self, tmp_path: Path):
        """二进制文件不存在时返回 False."""
        assert is_binary_available(SrTool.REALESRGAN, tmp_path) is False

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX 执行位")
    def test_binary_no_exec(self, tmp_path: Path):
        """文件存在但不可执行时返回 False."""
        binary_path = get_binary_path(SrTool.REALESRGAN, tmp_path)
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        binary_path.write_text("fake binary")
        binary_path.chmod(0o644)
        assert is_binary_available(SrTool.REALESRGAN, tmp_path) is False

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX 执行位")
    def test_binary_available(self, tmp_path: Path):
        """文件存在且可执行时返回 True."""
        binary_path = get_binary_path(SrTool.REALESRGAN, tmp_path)
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        binary_path.write_text("#!/bin/sh\necho ok")
        binary_path.chmod(binary_path.stat().st_mode | stat.S_IXUSR)
        assert is_binary_available(SrTool.REALESRGAN, tmp_path) is True

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows: 无 POSIX 执行位, 文件存在即可用")
    def test_binary_present_is_available_windows(self, tmp_path: Path):
        """Windows 上已下载 (文件存在) 即视为就绪, 不依赖执行位."""
        binary_path = get_binary_path(SrTool.REALESRGAN, tmp_path)
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        binary_path.write_text("fake binary")
        assert is_binary_available(SrTool.REALESRGAN, tmp_path) is True


class TestFindCommonPrefix:
    """公共目录前缀查找."""

    def test_single_dir_prefix(self):
        from amane.sr.binary import _find_common_prefix

        names = ["realesrgan-ncnn-vulkan/realesrgan-ncnn-vulkan", "realesrgan-ncnn-vulkan/models/"]
        assert _find_common_prefix(names) == "realesrgan-ncnn-vulkan/"

    def test_no_common_prefix(self):
        from amane.sr.binary import _find_common_prefix

        names = ["file1.txt", "file2.txt"]
        assert _find_common_prefix(names) is None

    def test_empty_list(self):
        from amane.sr.binary import _find_common_prefix

        assert _find_common_prefix([]) is None

"""binary 模块测试 - 下载包选择, 路径生成, 可用性检查."""

import platform
import stat
import sys
from pathlib import Path

import pytest

from amane.sr import SrTool, get_binary_path, get_download_url, is_binary_available
from amane.sr.tool import normalize_arch


class TestGetDownloadUrl:
    """按平台与架构选择下载包."""

    def test_linux_uses_self_built_packages(self):
        """linux 两个架构都取自建包: 上游无 arm64 包, 且其纯 CPU 模式段错误."""
        arm64 = get_download_url(SrTool.WAIFU2X, "linux", "aarch64")
        x86_64 = get_download_url(SrTool.WAIFU2X, "linux", "x86_64")
        for url in (arm64, x86_64):
            assert "/sqzw-x/waifu2x-ncnn-vulkan/" in url
        assert arm64.endswith("-linux-arm64.zip")
        assert x86_64.endswith("-linux-x86_64.zip")

    def test_arch_agnostic_platform_shares_one_package(self):
        """平台包与架构无关时, 各架构取同一个包."""
        for tool in SrTool:
            assert get_download_url(tool, "darwin", "arm64") == get_download_url(tool, "darwin", "x86_64")

    def test_machine_alias_normalized(self):
        """AMD64 与 x86_64 等价, ARM64 与 aarch64 等价."""
        assert get_download_url(SrTool.WAIFU2X, "win32", "AMD64") == get_download_url(SrTool.WAIFU2X, "win32", "x86_64")
        assert get_download_url(SrTool.WAIFU2X, "linux", "ARM64") == get_download_url(
            SrTool.WAIFU2X, "linux", "aarch64"
        )

    @pytest.mark.parametrize("tool", list(SrTool))
    def test_current_platform_supported(self, tool: SrTool):
        """运行本测试的平台必须能取到包."""
        assert get_download_url(tool, sys.platform, platform.machine()).startswith("https://")

    @pytest.mark.parametrize(
        ("tool", "target", "machine"),
        [
            (SrTool.REALESRGAN, "linux", "aarch64"),
            (SrTool.WAIFU2X, "linux", "riscv64"),
            (SrTool.WAIFU2X, "freebsd", "x86_64"),
            (SrTool.WAIFU2X, "linux", ""),
        ],
    )
    def test_unsupported_target(self, tool: SrTool, target: str, machine: str):
        """无可用包时报错, 并在信息里指明平台与架构."""
        with pytest.raises(RuntimeError, match=f"{target} {machine}"):
            get_download_url(tool, target, machine)


class TestNormalizeArch:
    """架构名归一."""

    @pytest.mark.parametrize(
        ("machine", "expected"),
        [
            ("x86_64", "x86_64"),
            ("AMD64", "x86_64"),
            ("arm64", "aarch64"),
            ("aarch64", "aarch64"),
            ("RISCV64", "riscv64"),
        ],
    )
    def test_normalize(self, machine: str, expected: str):
        assert normalize_arch(machine) == expected


class TestIsBinaryAvailable:
    """二进制可用性检查."""

    def test_missing_binary(self, tmp_path: Path):
        """二进制文件不存在时返回 False."""
        assert is_binary_available(SrTool.REALESRGAN, tmp_path) is False

    def test_cached_binary_not_reused_across_arch(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """换架构后不复用异构缓存: 旧架构的二进制不算可用."""
        monkeypatch.setattr(platform, "machine", lambda: "x86_64")
        cached = get_binary_path(SrTool.WAIFU2X, tmp_path)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text("#!/bin/sh\necho ok")
        cached.chmod(cached.stat().st_mode | stat.S_IXUSR)
        assert is_binary_available(SrTool.WAIFU2X, tmp_path) is True

        monkeypatch.setattr(platform, "machine", lambda: "aarch64")
        assert get_binary_path(SrTool.WAIFU2X, tmp_path) != cached
        assert is_binary_available(SrTool.WAIFU2X, tmp_path) is False

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

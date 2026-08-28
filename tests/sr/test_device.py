"""Vulkan ICD 探测与镜像捆绑二进制路径."""

import os
from pathlib import Path

import pytest

from amane.sr.binary import get_bundled_binary_path
from amane.sr.device import has_vulkan_icd
from amane.sr.tool import SrTool


class TestHasVulkanIcd:
    def test_explicit_missing_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setenv("VK_ICD_FILENAMES", str(tmp_path / "nope.json"))
        monkeypatch.setattr("amane.sr.device.sys.platform", "linux")
        assert has_vulkan_icd() is False

    def test_explicit_existing_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        icd = tmp_path / "lvp.json"
        icd.write_text("{}")
        monkeypatch.setenv("VK_ICD_FILENAMES", str(icd))
        monkeypatch.setattr("amane.sr.device.sys.platform", "linux")
        assert has_vulkan_icd() is True

    def test_explicit_pathsep_list(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        icd = tmp_path / "lvp.json"
        icd.write_text("{}")
        monkeypatch.setenv("VK_ICD_FILENAMES", os.pathsep.join([str(tmp_path / "missing.json"), str(icd)]))
        monkeypatch.setattr("amane.sr.device.sys.platform", "linux")
        assert has_vulkan_icd() is True

    def test_home_icd_json(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.delenv("VK_ICD_FILENAMES", raising=False)
        monkeypatch.setattr("amane.sr.device.sys.platform", "linux")
        dest = tmp_path / ".local/share/vulkan/icd.d"
        dest.mkdir(parents=True)
        (dest / "lvp.json").write_text("{}")
        monkeypatch.setattr("amane.sr.device.Path.home", lambda: tmp_path)
        assert has_vulkan_icd() is True

    def test_empty_home_icd_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.delenv("VK_ICD_FILENAMES", raising=False)
        monkeypatch.setattr("amane.sr.device.sys.platform", "linux")
        (tmp_path / ".local/share/vulkan/icd.d").mkdir(parents=True)
        monkeypatch.setattr("amane.sr.device.Path.home", lambda: tmp_path)
        assert has_vulkan_icd() is False


class TestGetBundledBinaryPath:
    def test_missing_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setenv("AMANE_SR_BUNDLE_DIR", str(tmp_path / "nope"))
        assert get_bundled_binary_path(SrTool.WAIFU2X) is None

    def test_waifu2x_present(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        bin_path = tmp_path / "waifu2x" / "waifu2x-ncnn-vulkan"
        bin_path.parent.mkdir()
        bin_path.write_text("#!/bin/sh\n")
        bin_path.chmod(0o755)
        monkeypatch.setenv("AMANE_SR_BUNDLE_DIR", str(tmp_path))
        assert get_bundled_binary_path(SrTool.WAIFU2X) == bin_path

    def test_realesrgan_not_bundled(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        (tmp_path / "waifu2x").mkdir()
        monkeypatch.setenv("AMANE_SR_BUNDLE_DIR", str(tmp_path))
        assert get_bundled_binary_path(SrTool.REALESRGAN) is None

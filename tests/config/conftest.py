"""config 测试共用 fixtures."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from amane.config import ColdSettings, ConfigManager


@pytest.fixture
def mgr(tmp_path: Path) -> ConfigManager:
    """以 ``tmp_path`` 为 data_dir 的新 ``ConfigManager``."""
    with patch.dict(os.environ, {"AMANE_DATA_DIR": str(tmp_path)}, clear=False):
        cold = ColdSettings()
    return ConfigManager.with_cold(cold)

"""API schema 模型测试"""

import pytest
from pydantic import TypeAdapter, ValidationError

from amane.api.models import (
    LibraryCreateRequest,
    MediaFileResponse,
    OrganizeSubmission,
    RefreshSubmission,
    ScrapeSubmission,
    TaskSubmission,
)
from amane.enums import LibraryAutomation
from amane.handlers.models import LibraryBase


class TestMediaFileResponse:
    def test_from_dict(self):
        data = {
            "id": 1,
            "path": "/media/video.mp4",
            "number": "MIDV-123",
            "status": "pending",
            "size": 1500000000,
        }
        resp = MediaFileResponse(**data)
        assert resp.id == 1
        assert resp.number == "MIDV-123"
        assert resp.status == "pending"


class Test_LibraryScoped:
    def test_with_library_id(self):
        req = LibraryBase(library_id=42)
        assert req.library_id == 42
        assert req.recursive is None
        assert req.patterns is None

    def test_overrides(self):
        req = LibraryBase(library_id=1, recursive=False, patterns=["*.mp4"])
        assert req.recursive is False
        assert req.patterns == ["*.mp4"]

    def test_library_id_required(self):
        with pytest.raises(ValidationError):
            LibraryBase.model_validate({})


class TestLibraryCreateRequest:
    def test_minimal(self):
        req = LibraryCreateRequest(path="/media/incoming")
        assert req.automation == "scrape"
        assert req.recursive is True
        assert req.patterns == []
        assert req.move_mode == "move"
        assert req.link_mode == "strm"
        assert req.link_template is None

    def test_full(self):
        req = LibraryCreateRequest(
            name="X",
            path="/media/x",
            automation=LibraryAutomation.NONE,
            recursive=False,
            patterns=["*.mp4", "*.mkv"],
            video_template="/out/{number}/{number}.{ext}",
        )
        assert req.patterns == ["*.mp4", "*.mkv"]


class TestTaskSubmission:
    adapter = TypeAdapter(TaskSubmission)

    def test_dispatch_refresh(self):
        req = self.adapter.validate_python({"type": "refresh", "library_id": 3})
        assert isinstance(req, RefreshSubmission)
        assert req.type == "refresh"
        assert req.library_id == 3
        assert req.scan == {"add"}  # 默认 scan 模式
        assert req.scrape == {"pending"}  # 默认 scrape 状态

    def test_dispatch_organize(self):
        req = self.adapter.validate_python({"type": "organize", "library_id": 7})
        assert isinstance(req, OrganizeSubmission)
        assert req.type == "organize"
        assert req.library_id == 7
        assert req.write_nfo is None
        assert req.copy_resources is None

    def test_dispatch_scrape(self):
        req = self.adapter.validate_python({"type": "scrape", "number": "MIDV-001"})
        assert isinstance(req, ScrapeSubmission)
        assert req.type == "scrape"
        assert req.number == "MIDV-001"

    def test_dispatch_actor_scrape(self):
        req = self.adapter.validate_python({"type": "actor_scrape", "actor_id": 42})
        assert req.type == "actor_scrape"
        assert req.actor_id == 42

    def test_unknown_type_rejected(self):
        with pytest.raises(ValidationError):
            self.adapter.validate_python({"type": "unknown"})

    def test_missing_type_rejected(self):
        with pytest.raises(ValidationError):
            self.adapter.validate_python({"path": "/foo"})

    def test_scan_requires_library_id(self):
        # library_id 必填: 缺失应被拒绝
        with pytest.raises(ValidationError):
            self.adapter.validate_python({"type": "refresh"})

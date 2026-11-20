"""API schema: 任务联合体分发与必填校验."""

import pytest
from pydantic import TypeAdapter, ValidationError

from amane.api.models import OrganizeSubmission, RefreshSubmission, ScrapeSubmission, TaskSubmission
from amane.handlers.models import LibraryBase, OrganizePayload


class Test_LibraryScoped:
    def test_library_id_required(self):
        with pytest.raises(ValidationError):
            LibraryBase.model_validate({})

    def test_organize_ignores_scan_fields(self):
        payload = OrganizePayload.model_validate({"library_id": 1, "recursive": False, "patterns": ["*.mkv"]})
        dumped = payload.model_dump()
        assert "recursive" not in dumped
        assert "patterns" not in dumped


class TestTaskSubmission:
    adapter = TypeAdapter(TaskSubmission)

    def test_dispatch_refresh(self):
        req = self.adapter.validate_python({"type": "refresh", "library_id": 3})
        assert isinstance(req, RefreshSubmission)
        assert req.library_id == 3
        assert req.scan == {"add"}
        assert req.scrape == {"pending"}

    def test_dispatch_organize(self):
        req = self.adapter.validate_python({"type": "organize", "library_id": 7})
        assert isinstance(req, OrganizeSubmission)
        assert req.library_id == 7

    def test_dispatch_scrape(self):
        req = self.adapter.validate_python({"type": "scrape", "number": "MIDV-001"})
        assert isinstance(req, ScrapeSubmission)
        assert req.number == "MIDV-001"

    def test_scrape_by_url_builds_payload(self):
        req = self.adapter.validate_python({"type": "scrape", "url": " https://javdb.com/v/abcd1 "})
        assert isinstance(req, ScrapeSubmission)
        assert req.url == "https://javdb.com/v/abcd1"
        payload = req.build_payload(None)
        assert payload is not None
        assert payload.url == "https://javdb.com/v/abcd1"
        assert payload.number == ""
        assert payload.content_type is None

    @pytest.mark.parametrize(
        "body",
        [
            {"type": "scrape"},
            {"type": "scrape", "number": "MIDV-001", "url": "https://javdb.com/v/abcd1"},
            {"type": "scrape", "number": "https://javdb.com/v/abcd1"},
            {"type": "scrape", "url": "ftp://javdb.com/v/abcd1"},
            {"type": "scrape", "url": "javdb.com/v/abcd1"},
        ],
    )
    def test_scrape_target_is_exclusive_http_url(self, body: dict[str, str]):
        with pytest.raises(ValidationError):
            self.adapter.validate_python(body)

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
        with pytest.raises(ValidationError):
            self.adapter.validate_python({"type": "refresh"})

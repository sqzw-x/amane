"""测试 NFO 附属文件生成"""

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

import pytest

from amane.db.models import Metadata
from amane.media import write_nfo

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def metadata() -> Metadata:
    return Metadata(
        number="MIDV-123",
        title="Test Title",
        actors=["Actor A", "Actor B"],
        studio="Studio X",
        release="2026-01-15",
        runtime=120,
        tags=["Drama", "Romance"],
        series="Series Y",
        scores={"javdb": 85.0},
        directors=["Director Z"],
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_has_required_fields(tmp_path: Path, metadata: Metadata):
    nfo_path = tmp_path / "MIDV-123.nfo"
    ok = await write_nfo(metadata, nfo_path)
    assert ok is True
    assert nfo_path.exists()
    content = nfo_path.read_text(encoding="utf-8")
    assert "<title>MIDV-123 Test Title</title>" in content
    assert "<num>MIDV-123</num>" in content
    assert "<actor>" in content
    assert "<name>Actor A</name>" in content
    assert "<studio>Studio X</studio>" in content
    assert "<genre>Drama</genre>" in content
    assert "<director>Director Z</director>" in content
    assert "<rating>85.0</rating>" in content
    # 系列使用规范嵌套: <set><name>…</name></set>
    assert "<series>Series Y</series>" in content
    assert "<set>" in content
    assert "<name>Series Y</name>" in content


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_plot_is_plain_text(tmp_path: Path):
    """长文本按纯文本写出: 无 CDATA, HTML 转义, 非法字符剥离, 换行原样保留."""
    plot = "结尾 ]]> 之后 & <标签>\n\n第二段\x0b"
    nfo_path = tmp_path / "PLOT-1.nfo"

    assert await write_nfo(Metadata(number="PLOT-1", title="T", plot=plot), nfo_path) is True

    content = nfo_path.read_text(encoding="utf-8")
    assert "<![CDATA[" not in content
    assert "]]>" not in content
    assert "\x0b" not in content

    root = ET.fromstring(content)
    assert root.findtext("plot") == "结尾 ]]> 之后 & <标签>\n\n第二段"
    assert root.findtext("outline") == root.findtext("plot")


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_uses_library_content_template(tmp_path: Path, metadata: Metadata):
    nfo_path = tmp_path / "custom.nfo"
    ok = await write_nfo(metadata, nfo_path, content_template="<movie><t>{number} {title}</t>{xml_actor}</movie>")

    assert ok is True
    root = ET.fromstring(nfo_path.read_text(encoding="utf-8"))
    assert root.findtext("t") == "MIDV-123 Test Title"
    assert [node.findtext("name") for node in root.findall("actor")] == ["Actor A", "Actor B"]


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_blank_template_falls_back_to_default(tmp_path: Path, metadata: Metadata):
    """空白模板与未设置等价, 仍写出完整默认正文."""
    blank_path = tmp_path / "blank.nfo"
    assert await write_nfo(metadata, blank_path, content_template="  \n ") is True
    assert "<mpaa>JP-18+</mpaa>" in blank_path.read_text(encoding="utf-8")


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_rejects_non_xml_content(tmp_path: Path):
    """渲染结果不是良构 XML 时不写文件 (写出前的兜底, 保存期校验才是第一道)."""
    nfo_path = tmp_path / "broken.nfo"

    assert (
        await write_nfo(Metadata(number="BROKEN-1"), nfo_path, content_template="<movie><t>{number}</movie>") is False
    )
    assert not nfo_path.exists()

from pathlib import Path

import pytest
from pptx import Presentation

from app.config import get_settings
from app.core.errors import ApiError, ErrorCode
from app.services.template_inspector import (
    SLIDE_TYPES,
    inspect_template,
    load_manifest,
    rebuild_manifest,
    save_manifest,
)


@pytest.fixture
def default_template_path(tmp_path: Path) -> Path:
    prs = Presentation()
    file_path = tmp_path / "default_template.pptx"
    prs.save(str(file_path))
    return file_path


def test_all_ten_slide_types_mapped(default_template_path: Path):
    manifest = inspect_template(default_template_path, "tmpl_test_1", "Test Template")
    assert len(manifest.layout_map) == 10
    for st in SLIDE_TYPES:
        assert st in manifest.layout_map
        assert st in manifest.slots
        assert st in manifest.limits
        assert manifest.slots[st].title_idx is not None


def test_architecture_and_demo_map_to_picture_layout(default_template_path: Path):
    manifest = inspect_template(default_template_path, "tmpl_test_2", "Test Template")
    arch_slot = manifest.slots["architecture"]
    demo_slot = manifest.slots["demo"]
    # Both architecture and demo must have picture_idx mapped
    assert arch_slot.picture_idx is not None
    assert demo_slot.picture_idx is not None


def test_title_and_closing_bullets_max_zero_on_title_slide(default_template_path: Path):
    manifest = inspect_template(default_template_path, "tmpl_test_3", "Test Template")
    assert manifest.slots["title"].body_idx is None
    assert manifest.limits["title"].bullets_max == 0
    assert manifest.slots["closing"].body_idx is None
    assert manifest.limits["closing"].bullets_max == 0


def test_rebuild_manifest_layout_override(default_template_path: Path):
    manifest = inspect_template(default_template_path, "tmpl_test_4", "Test Template")

    # Originally title maps to Title Slide (layout index 0) with no body_idx and bullets_max == 0
    assert manifest.layout_map["title"] == 0
    assert manifest.slots["title"].body_idx is None
    assert manifest.limits["title"].bullets_max == 0

    # Override title to layout index 1 ("Title and Content")
    override_map = dict(manifest.layout_map)
    override_map["title"] = 1
    rebuilt = rebuild_manifest(manifest, override_map)

    assert rebuilt.layout_map["title"] == 1
    assert rebuilt.slots["title"].body_idx is not None
    assert rebuilt.limits["title"].bullets_max > 0


def test_blank_layout_only_raises_template_invalid(tmp_path: Path):
    prs = Presentation()
    master = prs.slide_masters[0]
    for layout in list(master.slide_layouts):
        if layout.name != "Blank":
            master.slide_layouts.remove(layout)

    blank_path = tmp_path / "blank_only.pptx"
    prs.save(str(blank_path))

    with pytest.raises(ApiError) as exc_info:
        inspect_template(blank_path, "blank_tmpl", "Blank Template")
    assert exc_info.value.code == ErrorCode.TEMPLATE_INVALID

    # Nonexistent file also raises TEMPLATE_INVALID
    with pytest.raises(ApiError) as exc_info2:
        inspect_template(tmp_path / "does_not_exist.pptx", "none", "None")
    assert exc_info2.value.code == ErrorCode.TEMPLATE_INVALID


def test_save_load_manifest_round_trip(default_template_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path / "storage")

    manifest = inspect_template(default_template_path, "tmpl_roundtrip", "Roundtrip Template")
    saved_path = save_manifest(manifest)
    assert saved_path.exists()

    loaded = load_manifest("tmpl_roundtrip")
    assert loaded.template_id == manifest.template_id
    assert loaded.name == manifest.name
    assert loaded.slide_width == manifest.slide_width
    assert loaded.slide_height == manifest.slide_height
    assert loaded.layout_map == manifest.layout_map
    assert loaded.slots == manifest.slots
    assert loaded.limits == manifest.limits
    assert loaded.warnings == manifest.warnings

    # Nonexistent template_id raises NOT_FOUND
    with pytest.raises(ApiError) as exc_info:
        load_manifest("tmpl_missing_123")
    assert exc_info.value.code == ErrorCode.NOT_FOUND

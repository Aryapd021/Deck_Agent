import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation

from app.config import get_settings
from app.db import init_db
from app.main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_storage = tmp_path / "storage"
    test_templates = tmp_path / "templates"
    monkeypatch.setattr(get_settings(), "storage_dir", test_storage)
    monkeypatch.setattr(get_settings(), "_templates_dir", test_templates, raising=False)
    monkeypatch.setattr(type(get_settings()), "templates_dir", property(lambda self: test_templates))

    import app.db as db_mod
    db_mod._engine = None
    init_db()

    with TestClient(app) as test_client:
        yield test_client


def _make_default_pptx_bytes() -> bytes:
    prs = Presentation()
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def test_template_upload_list_override_workflow(client: TestClient):
    # 1. Upload valid template
    pptx_bytes = _make_default_pptx_bytes()
    res = client.post(
        "/api/templates",
        files={"file": ("presentation.pptx", pptx_bytes, "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        data={"name": "My Template"},
    )
    assert res.status_code == 201, res.text
    created = res.json()
    assert created["name"] == "My Template"
    assert "id" in created
    assert "manifest" in created
    template_id = created["id"]
    assert len(created["manifest"]["layout_map"]) == 10

    # 2. List templates
    list_res = client.get("/api/templates")
    assert list_res.status_code == 200
    templates_list = list_res.json()
    assert len(templates_list) >= 1
    assert templates_list[0]["id"] == template_id
    assert templates_list[0]["name"] == "My Template"

    # 3. Override one mapping via PUT /templates/{id}/layout-map
    put_res = client.put(
        f"/api/templates/{template_id}/layout-map",
        json={"layout_map": {"title": 1}},
    )
    assert put_res.status_code == 200
    updated = put_res.json()
    assert updated["manifest"]["layout_map"]["title"] == 1
    assert updated["manifest"]["slots"]["title"]["body_idx"] is not None

    # 4. Upload non-pptx file (.txt) expects 400 TEMPLATE_INVALID
    txt_res = client.post(
        "/api/templates",
        files={"file": ("readme.txt", b"plain text", "text/plain")},
    )
    assert txt_res.status_code == 400
    err_txt = txt_res.json()
    assert err_txt["code"] == "TEMPLATE_INVALID"
    assert "must be a .pptx file" in err_txt["message"]

    # 5. Unknown template id expects 404 NOT_FOUND
    missing_res = client.put(
        "/api/templates/nonexistent_id_123/layout-map",
        json={"layout_map": {"title": 0}},
    )
    assert missing_res.status_code == 404
    err_missing = missing_res.json()
    assert err_missing["code"] == "NOT_FOUND"

    # 6. Invalid slide type in layout-map expects 400 TEMPLATE_INVALID
    bad_type_res = client.put(
        f"/api/templates/{template_id}/layout-map",
        json={"layout_map": {"unknown_slide_type": 0}},
    )
    assert bad_type_res.status_code == 400
    assert bad_type_res.json()["code"] == "TEMPLATE_INVALID"

    # 7. Invalid layout index in layout-map expects 400 TEMPLATE_INVALID
    bad_idx_res = client.put(
        f"/api/templates/{template_id}/layout-map",
        json={"layout_map": {"title": 9999}},
    )
    assert bad_idx_res.status_code == 400
    assert bad_idx_res.json()["code"] == "TEMPLATE_INVALID"

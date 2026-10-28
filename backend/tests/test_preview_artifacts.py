from pathlib import Path
import shutil
import unittest.mock as mock

from fastapi.testclient import TestClient
from pptx import Presentation
import pytest

from app.config import Settings
from app.db import init_db, session_scope
from app.main import create_app
from app.models.tables import ArtifactRow, ProjectRow, TemplateRow
from app.services.preview import generate_previews
from tests.fixtures.sample_deck import build_sample_deck


@pytest.fixture
def test_client(tmp_path: Path):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    db_path = storage_dir / "deckagent.db"

    test_settings = Settings(
        storage_dir=storage_dir,
        database_url=f"sqlite:///{db_path.as_posix()}",
        preview_mode="libreoffice",
    )

    with mock.patch("app.config.get_settings", return_value=test_settings), \
         mock.patch("app.db.get_settings", return_value=test_settings), \
         mock.patch("app.core.storage.get_settings", return_value=test_settings), \
         mock.patch("app.api.artifacts.get_settings", return_value=test_settings), \
         mock.patch("app.services.preview.get_settings", return_value=test_settings):
        init_db()
        app = create_app()
        with TestClient(app) as client:
            yield client, storage_dir


def test_preview_skipped_when_outline_mode(tmp_path: Path):
    pptx_path = tmp_path / "test.pptx"
    Presentation().save(str(pptx_path))
    out_dir = tmp_path / "out"

    test_settings = Settings(
        storage_dir=tmp_path,
        preview_mode="outline",
    )
    with mock.patch("app.services.preview.get_settings", return_value=test_settings):
        result = generate_previews(pptx_path, out_dir)
        assert result == []


def test_preview_skipped_when_soffice_absent(tmp_path: Path):
    pptx_path = tmp_path / "test.pptx"
    Presentation().save(str(pptx_path))
    out_dir = tmp_path / "out"

    test_settings = Settings(
        storage_dir=tmp_path,
        preview_mode="libreoffice",
    )
    with mock.patch("app.services.preview.get_settings", return_value=test_settings), \
         mock.patch("shutil.which", return_value=None):
        result = generate_previews(pptx_path, out_dir)
        assert result == []


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice soffice binary is not installed")
def test_preview_with_soffice_produces_10_pngs(tmp_path: Path):
    pptx_path = tmp_path / "sample.pptx"
    prs = Presentation()
    for _ in range(9):
        prs.slides.add_slide(prs.slide_layouts[0])
    prs.save(str(pptx_path))

    out_dir = tmp_path / "previews"
    test_settings = Settings(
        storage_dir=tmp_path,
        preview_mode="libreoffice",
    )
    with mock.patch("app.services.preview.get_settings", return_value=test_settings):
        pngs = generate_previews(pptx_path, out_dir)
        assert len(pngs) == 10
        for png in pngs:
            assert png.exists()
            assert png.stat().st_size > 0


def test_artifact_routes(test_client):
    client, storage_dir = test_client

    project_id = "test_artifact_proj"
    template_id = "test_artifact_tmpl"
    version = 1

    # Populate Project and Template in DB
    with session_scope() as session:
        tmpl = TemplateRow(
            id=template_id,
            name="Template",
            path="templates/t.pptx",
            manifest_json="{}",
        )
        session.add(tmpl)
        session.flush()

        proj = ProjectRow(
            id=project_id,
            name="Project",
            repo_ref="./",
            template_id=template_id,
        )
        session.add(proj)

    # Create dummy artifact files on disk
    v_dir = storage_dir / "projects" / project_id / f"v{version}"
    v_dir.mkdir(parents=True, exist_ok=True)
    slides_dir = v_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)

    pptx_file = v_dir / "deck.pptx"
    pptx_file.write_bytes(b"PK\x03\x04dummy_pptx")

    diagram_file = v_dir / "diagram.png"
    diagram_file.write_bytes(b"\x89PNG\r\n\x1a\ndummy_diagram")

    slide_file = slides_dir / "1.png"
    slide_file.write_bytes(b"\x89PNG\r\n\x1a\ndummy_slide")

    # Insert ArtifactRow records in DB
    with session_scope() as session:
        session.add(
            ArtifactRow(
                project_id=project_id,
                version=version,
                kind="pptx",
                path=f"projects/{project_id}/v{version}/deck.pptx",
            )
        )
        session.add(
            ArtifactRow(
                project_id=project_id,
                version=version,
                kind="diagram",
                path=f"projects/{project_id}/v{version}/diagram.png",
            )
        )
        session.add(
            ArtifactRow(
                project_id=project_id,
                version=version,
                kind="slide_png",
                path=f"projects/{project_id}/v{version}/slides/1.png",
            )
        )

    # 1. GET deck.pptx -> 200, attachment filename deck_v1.pptx
    res_pptx = client.get(f"/api/projects/{project_id}/v/{version}/deck.pptx")
    assert res_pptx.status_code == 200
    assert "application/vnd.openxmlformats-officedocument.presentationml.presentation" in res_pptx.headers["content-type"]
    assert "deck_v1.pptx" in res_pptx.headers["content-disposition"]
    assert res_pptx.content == b"PK\x03\x04dummy_pptx"

    # 2. GET diagram.png -> 200, image/png
    res_diag = client.get(f"/api/projects/{project_id}/v/{version}/diagram.png")
    assert res_diag.status_code == 200
    assert "image/png" in res_diag.headers["content-type"]
    assert res_diag.content == b"\x89PNG\r\n\x1a\ndummy_diagram"

    # 3. GET slides/1.png -> 200, image/png
    res_slide = client.get(f"/api/projects/{project_id}/v/{version}/slides/1.png")
    assert res_slide.status_code == 200
    assert "image/png" in res_slide.headers["content-type"]
    assert res_slide.content == b"\x89PNG\r\n\x1a\ndummy_slide"

    # 4. Missing slide 2 -> 404
    res_missing_slide = client.get(f"/api/projects/{project_id}/v/{version}/slides/2.png")
    assert res_missing_slide.status_code == 404
    assert res_missing_slide.json()["code"] == "NOT_FOUND"

    # 5. Missing version 2 -> 404
    res_missing_v = client.get(f"/api/projects/{project_id}/v/2/deck.pptx")
    assert res_missing_v.status_code == 404
    assert res_missing_v.json()["code"] == "NOT_FOUND"

    # 6. Missing project -> 404
    res_missing_proj = client.get(f"/api/projects/unknown_proj/v/1/deck.pptx")
    assert res_missing_proj.status_code == 404
    assert res_missing_proj.json()["code"] == "NOT_FOUND"


def test_artifact_routes_path_traversal_and_invalid_inputs(test_client):
    client, _ = test_client

    # Path traversal in project ID is rejected with 404
    res1 = client.get("/api/projects/..%2F..%2Fetc/v/1/deck.pptx")
    assert res1.status_code in (404, 400)

    # Invalid characters in project ID
    res2 = client.get("/api/projects/bad*id/v/1/diagram.png")
    assert res2.status_code == 404

    # Non-positive version numbers
    res3 = client.get("/api/projects/valid_id/v/0/deck.pptx")
    assert res3.status_code == 404
    res4 = client.get("/api/projects/valid_id/v/-1/deck.pptx")
    assert res4.status_code == 404

    # Non-positive slide indices
    res5 = client.get("/api/projects/valid_id/v/1/slides/0.png")
    assert res5.status_code == 404
    res6 = client.get("/api/projects/valid_id/v/1/slides/-5.png")
    assert res6.status_code == 404

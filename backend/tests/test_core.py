import os
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core.errors import ApiError, ErrorCode, register_exception_handlers
from app.core.storage import (
    copy_version_dir,
    diagram_png_path,
    finalize_version_dir,
    new_temp_version_dir,
    pptx_path,
    project_dir,
    rel_to_storage,
    slide_png_path,
    template_manifest_path,
    template_pptx_path,
    validate_id,
    version_dir,
)
from app.db import get_engine, get_session, init_db, session_scope


def test_settings_resolve_storage_dir():
    orig_cwd = os.getcwd()
    try:
        backend_dir = get_settings().repo_root / "backend"
        os.chdir(backend_dir)

        # Storage dir with relative path resolves against repo root, NOT cwd (backend)
        s = Settings(storage_dir=Path("./storage"))
        assert s.storage_dir == s.repo_root / "storage"
        assert s.storage_dir.is_absolute()

        # Custom relative storage dir resolves against repo root
        s_custom = Settings(storage_dir=Path("custom_storage"))
        assert s_custom.storage_dir == s.repo_root / "custom_storage"
        assert s_custom.storage_dir.is_absolute()

        # Properties
        assert s.templates_dir == s.repo_root / "templates"
        assert s.db_path == s.storage_dir / "deckagent.db"
        assert s.frontend_dist == s.repo_root / "frontend" / "dist"

        # Secrets not in repr
        s_keys = Settings(
            gemini_api_key="supersecretgemini",
            anthropic_api_key="supersecretanthropic",
            github_token="supersecretgithub",
        )
        s_repr = repr(s_keys)
        assert "supersecretgemini" not in s_repr
        assert "supersecretanthropic" not in s_repr
        assert "supersecretgithub" not in s_repr
    finally:
        os.chdir(orig_cwd)


def test_storage_temp_dir_then_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Route storage_dir to tmp_path to isolate test
    test_storage = tmp_path / "storage"
    monkeypatch.setattr(get_settings(), "storage_dir", test_storage)

    pid = "test_proj_001"
    n = 1

    # 1. Create temp version dir
    tmp = new_temp_version_dir(pid, n)
    assert tmp.exists()
    assert tmp.name.startswith(f"v{n}.tmp-")
    assert tmp.parent == project_dir(pid)

    # Put a sample file in the temp dir
    sample_file = tmp / "deck.json"
    sample_file.write_text('{"schema_version": 1}', encoding="utf-8")

    # 2. Finalize version dir
    final = finalize_version_dir(tmp, pid, n)
    assert final == version_dir(pid, n)
    assert final.exists()
    assert not tmp.exists()
    assert (final / "deck.json").read_text(encoding="utf-8") == '{"schema_version": 1}'

    # 3. Refuse to overwrite existing v<N>
    tmp2 = new_temp_version_dir(pid, n)
    assert tmp2.exists()
    with pytest.raises(FileExistsError):
        finalize_version_dir(tmp2, pid, n)

    # 4. Copy version dir
    dst_tmp = new_temp_version_dir(pid, 2)
    copy_version_dir(pid, 1, dst_tmp)
    assert (dst_tmp / "deck.json").read_text(encoding="utf-8") == '{"schema_version": 1}'
    final2 = finalize_version_dir(dst_tmp, pid, 2)
    assert final2.exists()


def test_storage_id_guard():
    invalid_ids = ["../x", "foo/bar", "a\\b", "..", "", "bad id", "test@project", "foo;bar"]
    for bad_id in invalid_ids:
        with pytest.raises(ValueError):
            validate_id(bad_id)
        with pytest.raises(ValueError):
            project_dir(bad_id)
        with pytest.raises(ValueError):
            version_dir(bad_id, 1)
        with pytest.raises(ValueError):
            new_temp_version_dir(bad_id, 1)
        with pytest.raises(ValueError):
            template_pptx_path(bad_id)
        with pytest.raises(ValueError):
            template_manifest_path(bad_id)

    # Valid ids with alphanumeric, underscore, hyphen
    valid_ids = ["proj-1", "PROJ_2", "123", "a-b_c"]
    for good_id in valid_ids:
        assert validate_id(good_id) == good_id


def test_storage_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_storage = tmp_path / "storage"
    monkeypatch.setattr(get_settings(), "storage_dir", test_storage)

    pid = "proj123"
    v1 = version_dir(pid, 1)

    assert pptx_path(v1) == v1 / "deck.pptx"
    assert diagram_png_path(v1) == v1 / "diagram.png"
    assert slide_png_path(v1, 3) == v1 / "slides" / "3.png"
    # template_pptx_path resolves: writable storage first, then bundled candidates,
    # then falls back to writable.  When no file exists, it ends at either the
    # bundled templates_dir (if that dir itself exists) or the writable storage path.
    result = template_pptx_path("tmpl1")
    assert result.name == "tmpl1.pptx"
    assert template_manifest_path("tmpl1").name == "tmpl1.manifest.json"

    rel = rel_to_storage(v1 / "deck.pptx")
    assert rel == "projects/proj123/v1/deck.pptx"
    assert "/" in rel


def test_api_error_handler_envelope():
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/error/in-progress")
    def trigger_in_progress():
        raise ApiError(
            code=ErrorCode.JOB_IN_PROGRESS,
            message="A job is already running for this project",
            detail={"project_id": "p123"},
        )

    @app.get("/error/not-found")
    def trigger_not_found():
        raise ApiError(
            code=ErrorCode.NOT_FOUND,
            message="Item not found",
        )

    @app.get("/error/custom-status")
    def trigger_custom_status():
        raise ApiError(
            code=ErrorCode.LLM_INVALID_OUTPUT,
            message="LLM generated invalid format",
            status_code=502,
        )

    client = TestClient(app)

    # Test JOB_IN_PROGRESS (mapped to 409)
    res1 = client.get("/error/in-progress")
    assert res1.status_code == 409
    data1 = res1.json()
    assert data1 == {
        "code": "JOB_IN_PROGRESS",
        "message": "A job is already running for this project",
        "detail": {"project_id": "p123"},
    }

    # Test NOT_FOUND (mapped to 404, detail None)
    res2 = client.get("/error/not-found")
    assert res2.status_code == 404
    data2 = res2.json()
    assert data2 == {
        "code": "NOT_FOUND",
        "message": "Item not found",
        "detail": None,
    }

    # Test LLM_INVALID_OUTPUT (502)
    res3 = client.get("/error/custom-status")
    assert res3.status_code == 502
    data3 = res3.json()
    assert data3["code"] == "LLM_INVALID_OUTPUT"


def test_db_init_and_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_storage = tmp_path / "storage"
    monkeypatch.setattr(get_settings(), "storage_dir", test_storage)

    # Reset engine
    import app.db as db_mod
    db_mod._engine = None

    init_db()
    assert (test_storage / "deckagent.db").exists()

    # Test session_scope
    from sqlmodel import text

    with session_scope() as session:
        # Check SQLite pragma was executed
        res = session.exec(text("PRAGMA journal_mode;")).first()
        assert res[0].lower() == "wal"

    # Test get_session dependency
    gen = get_session()
    sess = next(gen)
    assert sess is not None
    sess.close()

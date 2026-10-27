from datetime import datetime, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import get_settings
from app.core.jobs import (
    _job_events_cache,
    _job_seqs,
    _job_subscribers,
    _job_versions,
    _project_locks,
    _running_tasks,
    release,
    try_acquire,
)
from app.db import get_engine, init_db, session_scope
from app.main import app
from app.models.tables import DeckVersionRow, ProjectInputRow, ProjectRow, TemplateRow
from tests.fixtures.sample_deck import build_sample_deck


@pytest.fixture(autouse=True)
def clean_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_storage = tmp_path / "storage"
    monkeypatch.setattr(get_settings(), "storage_dir", test_storage)

    import app.db as db_mod
    db_mod._engine = None
    init_db()

    # Clear in-memory job tracking structures
    _project_locks.clear()
    _running_tasks.clear()
    _job_subscribers.clear()
    _job_seqs.clear()
    _job_events_cache.clear()
    _job_versions.clear()

    yield

    _project_locks.clear()
    _running_tasks.clear()
    _job_subscribers.clear()
    _job_seqs.clear()
    _job_events_cache.clear()
    _job_versions.clear()
    db_mod._engine = None


def _create_template(template_id: str = "tmpl_test") -> TemplateRow:
    with session_scope() as session:
        tmpl = session.get(TemplateRow, template_id)
        if not tmpl:
            tmpl = TemplateRow(
                id=template_id,
                name="Test Template",
                path="templates/tmpl_test.pptx",
                manifest_json="{}",
                created_at=datetime.now(timezone.utc),
            )
            session.add(tmpl)
            session.commit()
            session.refresh(tmpl)
        return tmpl


def test_create_project_and_inputs_round_trip_with_secret_redaction():
    client = TestClient(app)
    _create_template("tmpl_test")

    # 1. Create project
    create_res = client.post(
        "/api/projects",
        json={
            "name": "Secret Project",
            "repo_path": "./",
            "template_id": "tmpl_test",
        },
    )
    assert create_res.status_code == 201
    proj_data = create_res.json()
    assert proj_data["name"] == "Secret Project"
    assert proj_data["repo_ref"] == "./"
    assert proj_data["template_id"] == "tmpl_test"
    project_id = proj_data["id"]

    # 2. Put inputs with secret tokens in chat_text
    secret_key = "sk-ant-api03-THISISVERYSECRETKEYOVER20CHARS"
    gh_token = "ghp_1234567890abcdef1234567890abcdef1234"
    chat_text = f"Discussion notes. Admin key is {secret_key} and github={gh_token}."

    input_res = client.put(
        f"/api/projects/{project_id}/inputs",
        json={
            "pitch_notes": "Pitch notes for executive team",
            "chat_text": chat_text,
            "criteria": "High quality deck",
        },
    )
    assert input_res.status_code == 200
    res_data = input_res.json()
    assert res_data["pitch_notes"] == "Pitch notes for executive team"
    assert res_data["has_chat"] is True
    assert res_data["redactions"] >= 2
    assert res_data["criteria"] == "High quality deck"

    # 3. Assert DB column has [REDACTED...] and raw secret never appears
    with session_scope() as session:
        row = session.get(ProjectInputRow, project_id)
        assert row is not None
        assert "[REDACTED:API_KEY]" in row.chat_redacted
        assert "[REDACTED:GITHUB_TOKEN]" in row.chat_redacted
        assert secret_key not in row.chat_redacted
        assert gh_token not in row.chat_redacted

    # 4. Assert raw secret appears nowhere in the DB file bytes (including WAL)
    settings = get_settings()
    db_bytes = settings.db_path.read_bytes()
    wal_path = settings.db_path.with_name(settings.db_path.name + "-wal")
    if wal_path.exists():
        db_bytes += wal_path.read_bytes()

    assert secret_key.encode("utf-8") not in db_bytes
    assert gh_token.encode("utf-8") not in db_bytes

    # 5. Test upsert by updating inputs
    update_res = client.put(
        f"/api/projects/{project_id}/inputs",
        json={
            "pitch_notes": "Updated pitch notes",
            "chat_text": "",
            "criteria": "Updated criteria",
        },
    )
    assert update_res.status_code == 200
    up_data = update_res.json()
    assert up_data["pitch_notes"] == "Updated pitch notes"
    assert up_data["has_chat"] is False
    assert up_data["redactions"] == 0


@pytest.mark.asyncio
async def test_second_generate_while_held_raises_409():
    client = TestClient(app)
    _create_template("tmpl_test")

    create_res = client.post(
        "/api/projects",
        json={
            "name": "Lock Test",
            "repo_path": "./",
            "template_id": "tmpl_test",
        },
    )
    assert create_res.status_code == 201
    project_id = create_res.json()["id"]

    # Acquire lock for project directly
    await try_acquire(project_id)

    # Second generate while lock is held -> 409 JOB_IN_PROGRESS
    gen_res = client.post(f"/api/projects/{project_id}/generate")
    assert gen_res.status_code == 409
    err = gen_res.json()
    assert err["code"] == "JOB_IN_PROGRESS"

    # Release lock and generate succeeds with 202
    release(project_id)
    gen_res2 = client.post(f"/api/projects/{project_id}/generate")
    assert gen_res2.status_code == 202
    assert "job_id" in gen_res2.json()


def test_missing_project_404():
    client = TestClient(app)

    # 404 for missing template on project creation
    create_res = client.post(
        "/api/projects",
        json={
            "name": "Missing Template",
            "repo_path": "./",
            "template_id": "nonexistent_tmpl",
        },
    )
    assert create_res.status_code == 404
    assert create_res.json()["code"] == "NOT_FOUND"

    # 404 for nonexistent project on inputs
    inp_res = client.put(
        "/api/projects/unknown_proj/inputs",
        json={"pitch_notes": "", "chat_text": "", "criteria": ""},
    )
    assert inp_res.status_code == 404
    assert inp_res.json()["code"] == "NOT_FOUND"

    # 404 for nonexistent project on generate
    gen_res = client.post("/api/projects/unknown_proj/generate")
    assert gen_res.status_code == 404
    assert gen_res.json()["code"] == "NOT_FOUND"

    # 404 for nonexistent project on deck
    deck_res = client.get("/api/projects/unknown_proj/deck")
    assert deck_res.status_code == 404
    assert deck_res.json()["code"] == "NOT_FOUND"


def test_get_deck_before_any_version_404_and_after_version_200():
    client = TestClient(app)
    _create_template("tmpl_test")

    create_res = client.post(
        "/api/projects",
        json={
            "name": "Deck Test",
            "repo_path": "./",
            "template_id": "tmpl_test",
        },
    )
    assert create_res.status_code == 201
    project_id = create_res.json()["id"]

    # 1. GET deck before any version exists -> 404 NOT_FOUND
    deck_res = client.get(f"/api/projects/{project_id}/deck")
    assert deck_res.status_code == 404
    assert deck_res.json()["code"] == "NOT_FOUND"

    # 2. Add DeckVersionRow
    sample_deck = build_sample_deck("tmpl_test", project_id)
    with session_scope() as session:
        version_row = DeckVersionRow(
            project_id=project_id,
            version=1,
            deck_json=sample_deck.model_dump_json(),
            created_by="generate",
            summary="Initial generated deck",
        )
        session.add(version_row)

    # 3. GET deck now succeeds with 200 DeckResponse
    deck_res2 = client.get(f"/api/projects/{project_id}/deck")
    assert deck_res2.status_code == 200
    data = deck_res2.json()
    assert data["version"] == 1
    assert data["created_by"] == "generate"
    assert data["summary"] == "Initial generated deck"
    assert "deck" in data
    assert len(data["deck"]["slides"]) == 10

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from app.main import create_app, lifespan
from app.models.tables import (
    ArtifactRow,
    DeckVersionRow,
    JobEventRow,
    JobRow,
    ProjectInputRow,
    ProjectRow,
    TemplateRow,
)


@pytest.fixture
def test_db_engine(tmp_path: Path):
    db_file = tmp_path / "test_tables.db"
    engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_all_seven_tables_exist(test_db_engine):
    table_names = set(SQLModel.metadata.tables.keys())
    expected = {
        "template",
        "project",
        "project_input",
        "deck_version",
        "job",
        "job_event",
        "artifact",
    }
    assert expected.issubset(table_names)


def test_deck_version_unique_constraint(test_db_engine):
    with Session(test_db_engine) as session:
        # Create template and project first
        tmpl = TemplateRow(
            id="t1",
            name="Template 1",
            path="templates/t1.pptx",
            manifest_json="{}",
        )
        proj = ProjectRow(
            id="p1",
            name="Project 1",
            repo_ref="repo/path",
            template_id="t1",
        )
        session.add(tmpl)
        session.commit()
        session.add(proj)
        session.commit()

        # Insert first DeckVersionRow
        v1 = DeckVersionRow(
            project_id="p1",
            version=1,
            deck_json='{"schema_version": 1}',
            created_by="generate",
            summary="Initial version",
        )
        session.add(v1)
        session.commit()

        # Insert second DeckVersionRow with same (project_id, version)
        v1_dup = DeckVersionRow(
            project_id="p1",
            version=1,
            deck_json='{"schema_version": 1, "dup": true}',
            created_by="edit",
            summary="Duplicate version",
        )
        session.add(v1_dup)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_job_event_unique_constraint(test_db_engine):
    with Session(test_db_engine) as session:
        tmpl = TemplateRow(id="t1", name="T", path="p", manifest_json="{}")
        proj = ProjectRow(id="p1", name="P", repo_ref="r", template_id="t1")
        job = JobRow(id="j1", project_id="p1", type="generate", status="running")
        session.add(tmpl)
        session.commit()
        session.add(proj)
        session.commit()
        session.add(job)
        session.commit()

        e1 = JobEventRow(
            job_id="j1",
            seq=1,
            stage="ingest",
            status="started",
            message="Ingesting repo",
        )
        session.add(e1)
        session.commit()

        e1_dup = JobEventRow(
            job_id="j1",
            seq=1,
            stage="ingest",
            status="progress",
            message="Duplicate seq",
        )
        session.add(e1_dup)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


@pytest.mark.asyncio
async def test_lifespan_orphaned_jobs_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_storage = tmp_path / "storage"
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "storage_dir", test_storage)

    # Reset engine
    import app.db as db_mod
    db_mod._engine = None

    db_mod.init_db()

    with db_mod.session_scope() as session:
        tmpl = TemplateRow(id="t_life", name="T", path="p", manifest_json="{}")
        proj = ProjectRow(id="p_life", name="P", repo_ref="r", template_id="t_life")
        running_job = JobRow(id="j_running", project_id="p_life", type="generate", status="running")
        done_job = JobRow(id="j_done", project_id="p_life", type="generate", status="done")
        session.add(tmpl)
        session.commit()
        session.add(proj)
        session.commit()
        session.add(running_job)
        session.add(done_job)
        session.commit()

    app = create_app()
    async with lifespan(app):
        pass

    with db_mod.session_scope() as session:
        j_run = session.get(JobRow, "j_running")
        j_dn = session.get(JobRow, "j_done")
        assert j_run.status == "error"
        assert j_run.error_code == "ORPHANED_JOB"
        assert j_run.ended_at is not None
        assert j_dn.status == "done"

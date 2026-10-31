from contextlib import contextmanager
from typing import Generator

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{settings.db_path.as_posix()}"
        _engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def init_db() -> None:
    settings = get_settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    try:
        import app.models.tables  # noqa: F401
    except ImportError:
        pass
    SQLModel.metadata.create_all(get_engine())

    # Seed default templates if database table is empty
    import json
    from datetime import datetime, timezone
    from sqlmodel import select, func
    from app.models.tables import TemplateRow

    with Session(get_engine()) as session:
        try:
            count = session.exec(select(func.count(TemplateRow.id))).one()
            if count == 0:
                candidate_dirs = [
                    Path(__file__).resolve().parents[1] / "templates",
                    settings.repo_root / "templates",
                ]
                bundled_dir = None
                for cd in candidate_dirs:
                    if cd.exists():
                        bundled_dir = cd
                        break

                if bundled_dir and bundled_dir.exists():
                    for pptx_file in bundled_dir.glob("*.pptx"):
                        tid = pptx_file.stem
                        m_file = bundled_dir / f"{tid}.manifest.json"
                        name = "Default Template" if any(k in tid.lower() for k in ("default", "tmpl_1")) else tid
                        m_json = "{}"
                        if m_file.exists():
                            try:
                                m_json = m_file.read_text(encoding="utf-8")
                                m_obj = json.loads(m_json)
                                name = m_obj.get("name", name)
                            except Exception:
                                pass
                        row = TemplateRow(
                            id=tid,
                            name=name,
                            manifest_json=m_json,
                            created_at=datetime.now(timezone.utc),
                        )
                        session.add(row)

                # Ensure an explicit 'default' template row exists
                has_default = session.get(TemplateRow, "default")
                if not has_default:
                    session.add(
                        TemplateRow(
                            id="default",
                            name="Default Template",
                            manifest_json="{}",
                            created_at=datetime.now(timezone.utc),
                        )
                    )
                session.commit()
        except Exception:
            session.rollback()


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = Session(get_engine())
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

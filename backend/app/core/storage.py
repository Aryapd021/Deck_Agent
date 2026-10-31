import os
import re
import shutil
import uuid
from pathlib import Path

from app.config import get_settings

_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_id(val: str, name: str = "id") -> str:
    if not isinstance(val, str) or not _ID_RE.match(val):
        raise ValueError(f"Invalid {name}: {val!r}. Must match ^[A-Za-z0-9_-]+$")
    return val


def project_dir(pid: str) -> Path:
    validate_id(pid, "project_id")
    return get_settings().storage_dir / "projects" / pid


def version_dir(pid: str, n: int) -> Path:
    validate_id(pid, "project_id")
    return project_dir(pid) / f"v{n}"


def new_temp_version_dir(pid: str, n: int) -> Path:
    validate_id(pid, "project_id")
    token = uuid.uuid4().hex
    p_dir = project_dir(pid)
    p_dir.mkdir(parents=True, exist_ok=True)
    tmp = p_dir / f"v{n}.tmp-{token}"
    tmp.mkdir(parents=True, exist_ok=False)
    return tmp


def finalize_version_dir(tmp: Path, pid: str, n: int) -> Path:
    validate_id(pid, "project_id")
    target = version_dir(pid, n)
    if target.exists():
        raise FileExistsError(f"Version directory already exists: {target}")
    os.replace(tmp, target)
    return target


def copy_version_dir(pid: str, src_n: int, dst_tmp: Path) -> Path:
    validate_id(pid, "project_id")
    src = version_dir(pid, src_n)
    if not src.exists():
        raise FileNotFoundError(f"Source version directory does not exist: {src}")
    dst_tmp.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst_tmp, dirs_exist_ok=True)
    return dst_tmp


def pptx_path(dir: Path) -> Path:
    return dir / "deck.pptx"


def diagram_png_path(dir: Path) -> Path:
    return dir / "diagram.png"


def slide_png_path(dir: Path, k: int) -> Path:
    return dir / "slides" / f"{k}.png"


def template_pptx_path(tid: str) -> Path:
    validate_id(tid, "template_id")
    candidates = [
        get_settings().templates_dir / f"{tid}.pptx",
        Path(__file__).resolve().parents[2] / "templates" / f"{tid}.pptx",
        get_settings().repo_root / "templates" / f"{tid}.pptx",
    ]
    for c in candidates:
        if c.exists():
            return c
    return get_settings().templates_dir / f"{tid}.pptx"


def template_manifest_path(tid: str) -> Path:
    validate_id(tid, "template_id")
    candidates = [
        get_settings().templates_dir / f"{tid}.manifest.json",
        Path(__file__).resolve().parents[2] / "templates" / f"{tid}.manifest.json",
        get_settings().repo_root / "templates" / f"{tid}.manifest.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    return get_settings().templates_dir / f"{tid}.manifest.json"


def rel_to_storage(path: Path | str) -> str:
    p = Path(path)
    s = get_settings().storage_dir
    if not p.is_absolute():
        p = (s / p).resolve()
    else:
        p = p.resolve()
    return p.relative_to(s.resolve()).as_posix()

import asyncio
import base64
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Literal

from app.config import get_settings
from app.core.errors import ApiError, ErrorCode
from app.models.facts import RepoDigest

PRUNED_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "out",
    "target",
    ".next",
    ".venv",
    "venv",
    "__pycache__",
    "coverage",
    "vendor",
    ".idea",
    ".vscode",
}

LOCKFILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pipfile.lock",
    "cargo.lock",
    "composer.lock",
    "go.sum",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tiff",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar",
    ".pdf", ".pptx", ".ppt", ".docx", ".doc", ".xlsx", ".xls",
    ".db", ".sqlite", ".sqlite3",
    ".bin", ".exe", ".dll", ".so", ".dylib", ".class", ".jar", ".war",
    ".pyc", ".pyo", ".pyd", ".iso",
    ".mp4", ".mp3", ".wav", ".avi", ".mov", ".mkv",
}

TARGET_MANIFESTS = {
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "go.mod",
    "cargo.toml",
    "pom.xml",
    "gemfile",
}

FRAMEWORK_KEYWORDS = [
    "fastapi",
    "flask",
    "django",
    "express",
    "react",
    "vue",
    "svelte",
    "next",
    "nuxt",
    "angular",
    "spring",
    "rails",
    "laravel",
    "gin",
    "echo",
    "fiber",
    "actix",
    "axum",
    "rocket",
    "nestjs",
    "fastify",
    "koa",
    "tornado",
    "aiohttp",
    "litestar",
]

ROUTE_MODEL_KEYWORDS = (
    "route",
    "router",
    "api",
    "controller",
    "views",
    "models",
    "schema",
    "endpoints",
    "handler",
)

MAX_FILE_SIZE = 200 * 1024  # 200 KB
MAX_KNOWN_PATHS = 3000


def _extract_repo_name(repo_ref: str) -> str:
    cleaned = repo_ref.strip().rstrip("/\\")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    # For ssh git@github.com:user/repo
    if ":" in cleaned and not (len(cleaned) > 1 and cleaned[1] == ":"):
        cleaned = cleaned.split(":")[-1]
    name = Path(cleaned).name
    return name or "repo"


def _scrub(text: str, token: str) -> str:
    scrubbed = text
    if token:
        scrubbed = scrubbed.replace(token, "***")
        raw_auth = f"x-access-token:{token}".encode("utf-8")
        b64_auth = base64.b64encode(raw_auth).decode("utf-8")
        scrubbed = scrubbed.replace(b64_auth, "***")
    return scrubbed


def _scan_repo_files(root: Path) -> tuple[list[str], dict[str, str], dict[str, int]]:
    known_paths: list[str] = []
    file_contents: dict[str, str] = {}
    languages: dict[str, int] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored directories in-place
        dirnames[:] = [
            d for d in dirnames
            if d.lower() not in PRUNED_DIRS and d not in PRUNED_DIRS
        ]

        for fname in sorted(filenames):
            if fname.lower() in LOCKFILES:
                continue

            fpath = Path(dirpath) / fname
            ext = fpath.suffix.lower()
            if ext in BINARY_EXTENSIONS:
                continue

            try:
                st = fpath.stat()
                if st.st_size > MAX_FILE_SIZE:
                    continue
            except OSError:
                continue

            try:
                content = fpath.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue

            try:
                rel_path = fpath.relative_to(root).as_posix()
            except ValueError:
                continue

            if len(known_paths) < MAX_KNOWN_PATHS:
                known_paths.append(rel_path)

            file_contents[rel_path] = content

            if ext:
                languages[ext] = languages.get(ext, 0) + 1

    known_paths.sort()
    return known_paths, file_contents, languages


def _build_repo_digest_sync(repo_ref: str) -> RepoDigest:
    ref_trimmed = repo_ref.strip()
    is_url = (
        ref_trimmed.startswith("http://")
        or ref_trimmed.startswith("https://")
        or ref_trimmed.startswith("git@")
    )
    repo_name = _extract_repo_name(ref_trimmed)
    source_kind: Literal["url", "path"] = "url" if is_url else "path"
    settings = get_settings()

    if is_url:
        if not shutil.which("git"):
            raise ApiError(ErrorCode.TOOL_MISSING, "git is not installed or not in PATH")

        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = ["git"]
            is_github_https = ref_trimmed.lower().startswith("https://") and "github.com" in ref_trimmed.lower()
            if settings.github_token and is_github_https:
                raw_auth = f"x-access-token:{settings.github_token}".encode("utf-8")
                b64_auth = base64.b64encode(raw_auth).decode("utf-8")
                cmd.extend(["-c", f"http.extraHeader=Authorization: Basic {b64_auth}"])

            cmd.extend(["clone", "--depth", "1", ref_trimmed, tmpdir])
            env = dict(os.environ)
            env["GIT_TERMINAL_PROMPT"] = "0"

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=env,
                )
                if proc.returncode != 0:
                    raw_err = proc.stderr or proc.stdout or "git clone failed"
                    scrubbed_err = _scrub(raw_err, settings.github_token)
                    raise ApiError(ErrorCode.REPO_UNREACHABLE, f"Failed to clone repository: {scrubbed_err}")
            except subprocess.TimeoutExpired as e:
                raise ApiError(ErrorCode.REPO_UNREACHABLE, "Git clone timed out after 120s") from e
            except ApiError:
                raise
            except Exception as e:
                scrubbed_err = _scrub(str(e), settings.github_token)
                raise ApiError(ErrorCode.REPO_UNREACHABLE, f"Git clone failed: {scrubbed_err}") from e

            return _process_repo_root(
                root_path=Path(tmpdir),
                repo_name=repo_name,
                source_kind=source_kind,
                max_tokens=settings.max_repo_tokens,
            )

    else:
        local_path = Path(ref_trimmed)
        if not local_path.is_absolute():
            candidate = (settings.repo_root / local_path).resolve()
            if candidate.exists():
                local_path = candidate
            else:
                local_path = local_path.resolve()
        else:
            local_path = local_path.resolve()

        if not local_path.exists() or not local_path.is_dir():
            raise ApiError(
                ErrorCode.REPO_UNREACHABLE,
                f"Local repository path does not exist or is not a directory: {ref_trimmed}",
            )

        return _process_repo_root(
            root_path=local_path,
            repo_name=repo_name,
            source_kind=source_kind,
            max_tokens=settings.max_repo_tokens,
        )


def _process_repo_root(
    root_path: Path,
    repo_name: str,
    source_kind: Literal["url", "path"],
    max_tokens: int,
) -> RepoDigest:
    known_paths, file_contents, languages = _scan_repo_files(root_path)
    if not known_paths:
        raise ApiError(
            ErrorCode.REPO_UNREACHABLE,
            "Repository contains no readable text files",
        )

    total_budget = max_tokens
    current_tokens = 0
    files_included: list[str] = []

    # 1) README excerpt (<= 3000 tokens)
    readme_excerpt: str | None = None
    readme_candidates = [
        p for p in known_paths
        if Path(p).stem.lower() == "readme"
    ]
    readme_candidates.sort(key=lambda p: (p.count("/"), p))

    scale = max(1.0, total_budget / 12000.0)

    if readme_candidates:
        readme_path = readme_candidates[0]
        readme_budget = min(int(3000 * scale), total_budget - current_tokens)
        if readme_budget > 0:
            raw_readme = file_contents[readme_path]
            max_chars = readme_budget * 4
            if len(raw_readme) > max_chars:
                raw_readme = raw_readme[:max_chars]
            readme_excerpt = raw_readme
            current_tokens += len(readme_excerpt) // 4
            files_included.append(readme_path)

    # 2) dependency manifests (package.json, requirements.txt, pyproject.toml, plus go.mod, Cargo.toml, pom.xml, Gemfile if present)
    manifest_candidates = [
        p for p in known_paths
        if Path(p).name.lower() in TARGET_MANIFESTS
    ]
    manifest_candidates.sort(key=lambda p: (p.count("/"), p))

    manifest_budget = min(int(2500 * scale), total_budget - current_tokens)
    manifests: dict[str, str] = {}

    for mp in manifest_candidates:
        if manifest_budget <= 0:
            break
        raw_man = file_contents[mp]
        max_chars = manifest_budget * 4
        if len(raw_man) > max_chars:
            raw_man = raw_man[:max_chars]
        manifests[mp] = raw_man
        tokens = len(raw_man) // 4
        current_tokens += tokens
        manifest_budget -= tokens
        files_included.append(mp)

    # Frameworks keyword scan
    manifest_blob = " ".join(manifests.values()).lower()
    frameworks = [kw for kw in FRAMEWORK_KEYWORDS if kw in manifest_blob]

    # 3) top-level tree listing plus depth-2 entries, <= 150 lines
    tree_budget = min(int(1500 * scale), total_budget - current_tokens)
    tree_entries: set[str] = set()
    for p in known_paths:
        parts = p.split("/")
        if len(parts) == 1:
            tree_entries.add(parts[0])
        elif len(parts) == 2:
            tree_entries.add(f"{parts[0]}/")
            tree_entries.add(f"{parts[0]}/{parts[1]}")
        else:
            tree_entries.add(f"{parts[0]}/")
            tree_entries.add(f"{parts[0]}/{parts[1]}/")

    sorted_tree = sorted(tree_entries)[:150]
    tree_text = "\n".join(sorted_tree)
    if tree_budget > 0:
        max_tree_chars = tree_budget * 4
        if len(tree_text) > max_tree_chars:
            tree_text = tree_text[:max_tree_chars]
        current_tokens += len(tree_text) // 4
    else:
        tree_text = ""

    # 4) entrypoints named main.*, app.*, index.*, server.*, manage.py, at depth <= 3
    entrypoint_budget = min(int(2500 * scale), total_budget - current_tokens)
    entrypoint_candidates = []
    for p in known_paths:
        if p in files_included:
            continue
        parts = p.split("/")
        if len(parts) > 3:
            continue
        stem = Path(p).stem.lower()
        fname = Path(p).name.lower()
        if stem in ("main", "app", "index", "server") or fname == "manage.py":
            entrypoint_candidates.append(p)

    entrypoint_candidates.sort(key=lambda p: (p.count("/"), p))
    entrypoints: dict[str, str] = {}
    for ep in entrypoint_candidates:
        if entrypoint_budget <= 0:
            break
        raw_ep = file_contents[ep]
        max_chars = entrypoint_budget * 4
        if len(raw_ep) > max_chars:
            raw_ep = raw_ep[:max_chars]
        entrypoints[ep] = raw_ep
        tokens = len(raw_ep) // 4
        current_tokens += tokens
        entrypoint_budget -= tokens
        files_included.append(ep)

    # 5) route/model files whose name contains route, router, api, controller, views, models, schema, endpoints, handler
    route_budget = min(int(2500 * scale), total_budget - current_tokens)
    route_candidates = []
    for p in known_paths:
        if p in files_included:
            continue
        stem = Path(p).stem.lower()
        if any(kw in stem for kw in ROUTE_MODEL_KEYWORDS):
            route_candidates.append(p)

    route_candidates.sort(key=lambda p: (p.count("/"), p))
    route_files: dict[str, str] = {}
    for rp in route_candidates:
        if route_budget <= 0:
            break
        raw_rp = file_contents[rp]
        max_chars = route_budget * 4
        if len(raw_rp) > max_chars:
            raw_rp = raw_rp[:max_chars]
        route_files[rp] = raw_rp
        tokens = len(raw_rp) // 4
        current_tokens += tokens
        route_budget -= tokens
        files_included.append(rp)

    approx_tokens = (
        len(readme_excerpt or "")
        + sum(len(v) for v in manifests.values())
        + len(tree_text)
        + sum(len(v) for v in entrypoints.values())
        + sum(len(v) for v in route_files.values())
    ) // 4

    return RepoDigest(
        name=repo_name,
        source_kind=source_kind,
        tree=tree_text,
        languages=languages,
        frameworks=frameworks,
        manifests=manifests,
        readme_excerpt=readme_excerpt,
        entrypoints=entrypoints,
        route_files=route_files,
        files_included=files_included,
        known_paths=known_paths,
        approx_tokens=approx_tokens,
    )


async def build_repo_digest(repo_ref: str) -> RepoDigest:
    return await asyncio.to_thread(_build_repo_digest_sync, repo_ref)

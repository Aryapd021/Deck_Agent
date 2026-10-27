from pathlib import Path
import shutil
import pytest

from app.config import Settings
from app.core.errors import ApiError, ErrorCode
from app.services.ingest import build_repo_digest


@pytest.fixture
def repo_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "sample_repo"
    repo.mkdir()

    # 1. README.md
    (repo / "README.md").write_text("# Sample Project\nA test project for ingest.\nFeatures: fast, reliable.", encoding="utf-8")

    # 2. package.json with dependencies (express, react)
    (repo / "package.json").write_text(
        '{\n  "name": "sample-project",\n  "dependencies": {\n    "express": "^4.18.0",\n    "react": "^18.2.0"\n  }\n}',
        encoding="utf-8",
    )

    # 3. src/index.js (entrypoint)
    src = repo / "src"
    src.mkdir()
    (src / "index.js").write_text("console.log('App starting');\nconst express = require('express');", encoding="utf-8")

    # 4. src/api_routes.js (route/model)
    (src / "api_routes.js").write_text("const router = express.Router();\nrouter.get('/health', (req, res) => res.json({ok: true}));", encoding="utf-8")

    # 5. node_modules/x/y.js (must be pruned/excluded)
    nm = repo / "node_modules" / "x"
    nm.mkdir(parents=True)
    (nm / "y.js").write_text("module.exports = {};", encoding="utf-8")

    # 6. package-lock.json (must be excluded)
    (repo / "package-lock.json").write_text('{"lockfileVersion": 2}', encoding="utf-8")

    # 7. binary file (must be excluded)
    (repo / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01")

    # 8. file over 200 KB (must be excluded)
    (repo / "large.txt").write_text("a" * (205 * 1024), encoding="utf-8")

    # 9. non-utf8 file (must be excluded)
    (repo / "bad_encoding.txt").write_bytes(b"\xff\xfe\x00\x00\xfa\xfb")

    return repo


@pytest.mark.asyncio
async def test_local_path_and_exclusions(repo_fixture: Path) -> None:
    digest = await build_repo_digest(str(repo_fixture))

    assert digest.name == "sample_repo"
    assert digest.source_kind == "path"

    # Verify exclusions:
    assert not any("node_modules" in p for p in digest.known_paths)
    assert not any(p.endswith("package-lock.json") for p in digest.known_paths)
    assert not any(p.endswith(".png") for p in digest.known_paths)
    assert not any(p.endswith("large.txt") for p in digest.known_paths)
    assert not any(p.endswith("bad_encoding.txt") for p in digest.known_paths)

    # Verify known paths:
    expected_known = {"README.md", "package.json", "src/api_routes.js", "src/index.js"}
    assert set(digest.known_paths) == expected_known

    # Verify languages:
    assert digest.languages.get(".md") == 1
    assert digest.languages.get(".json") == 1
    assert digest.languages.get(".js") == 2

    # Verify frameworks detected from package.json:
    assert "express" in digest.frameworks
    assert "react" in digest.frameworks

    # Verify files included:
    assert "README.md" in digest.files_included
    assert "package.json" in digest.files_included
    assert "src/index.js" in digest.files_included
    assert "src/api_routes.js" in digest.files_included

    # Verify manifests, entrypoints, route_files:
    assert "package.json" in digest.manifests
    assert "src/index.js" in digest.entrypoints
    assert "src/api_routes.js" in digest.route_files
    assert digest.readme_excerpt is not None
    assert "Sample Project" in digest.readme_excerpt

    # Verify tree contains src/ and files
    assert "README.md" in digest.tree
    assert "package.json" in digest.tree
    assert "src/" in digest.tree

    assert digest.approx_tokens > 0


@pytest.mark.asyncio
async def test_token_cap_respected(repo_fixture: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Set tiny max_repo_tokens, e.g. 20 tokens (80 chars total)
    monkeypatch.setattr(
        "app.services.ingest.get_settings",
        lambda: Settings(max_repo_tokens=20),
    )

    digest = await build_repo_digest(str(repo_fixture))
    assert digest.approx_tokens <= 20


@pytest.mark.asyncio
async def test_nonexistent_local_path() -> None:
    with pytest.raises(ApiError) as exc_info:
        await build_repo_digest("C:/nonexistent_dir_12345/dummy")
    assert exc_info.value.code == ErrorCode.REPO_UNREACHABLE


@pytest.mark.asyncio
async def test_nonsense_url() -> None:
    if not shutil.which("git"):
        pytest.skip("git not installed")

    with pytest.raises(ApiError) as exc_info:
        await build_repo_digest("https://localhost.invalid/nonsense/repo.git")
    assert exc_info.value.code == ErrorCode.REPO_UNREACHABLE

from functools import lru_cache
import os
from pathlib import Path
import shutil
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_tool_paths() -> None:
    if shutil.which("dot") is not None:
        return
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Graphviz" / "Graphviz-15.1.1-win64" / "bin",
        Path("C:/Program Files/Graphviz/bin"),
        Path("C:/Program Files (x86)/Graphviz/bin"),
    ]
    local_app_data = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Graphviz"
    if local_app_data.exists():
        for d in local_app_data.glob("*/bin"):
            candidates.append(d)

    for c in candidates:
        if (c / "dot.exe").exists() or (c / "dot").exists():
            os.environ["PATH"] = str(c) + os.pathsep + os.environ.get("PATH", "")
            break


_ensure_tool_paths()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        extra="ignore",
    )

    llm_provider: Literal["gemini", "anthropic"] = "gemini"
    gemini_api_key: str = Field(default="", repr=False)
    anthropic_api_key: str = Field(default="", repr=False)
    github_token: str = Field(default="", repr=False)
    storage_dir: Path = Path("./storage")
    preview_mode: Literal["libreoffice", "outline"] = "libreoffice"
    max_repo_tokens: int = 20000
    max_slides: int = 10
    gemini_model: str = "gemini-2.5-flash"
    anthropic_model: str = "claude-3-7-sonnet-20250219"
    design_engine: bool = True

    @field_validator("storage_dir", mode="after")
    @classmethod
    def _resolve_storage_dir(cls, v: Path) -> Path:
        if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            tmp_storage = Path("/tmp/storage")
            tmp_storage.mkdir(parents=True, exist_ok=True)
            return tmp_storage

        resolved = (REPO_ROOT / v).resolve() if not v.is_absolute() else v.resolve()
        try:
            resolved.mkdir(parents=True, exist_ok=True)
            test_file = resolved / ".write_test"
            test_file.touch()
            test_file.unlink()
            return resolved
        except OSError:
            tmp_storage = Path("/tmp/storage")
            tmp_storage.mkdir(parents=True, exist_ok=True)
            return tmp_storage

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    @property
    def templates_dir(self) -> Path:
        if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            p = self.storage_dir / "templates"
            p.mkdir(parents=True, exist_ok=True)
            return p
        return REPO_ROOT / "templates"

    @property
    def db_path(self) -> Path:
        return self.storage_dir / "deckagent.db"

    @property
    def frontend_dist(self) -> Path:
        return REPO_ROOT / "frontend" / "dist"

    def __repr__(self) -> str:
        # Never include secrets in repr
        fields = [
            f"llm_provider={self.llm_provider!r}",
            "gemini_api_key='***'" if self.gemini_api_key else "gemini_api_key=''",
            "anthropic_api_key='***'" if self.anthropic_api_key else "anthropic_api_key=''",
            "github_token='***'" if self.github_token else "github_token=''",
            f"storage_dir={self.storage_dir!r}",
            f"preview_mode={self.preview_mode!r}",
            f"max_repo_tokens={self.max_repo_tokens}",
            f"max_slides={self.max_slides}",
            f"gemini_model={self.gemini_model!r}",
            f"anthropic_model={self.anthropic_model!r}",
        ]
        return f"Settings({', '.join(fields)})"


@lru_cache
def get_settings() -> Settings:
    return Settings()

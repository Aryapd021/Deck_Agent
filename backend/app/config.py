from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


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
    max_repo_tokens: int = 12000
    max_slides: int = 10
    gemini_model: str = "gemini-2.5-flash"
    anthropic_model: str = "claude-3-7-sonnet-20250219"

    @field_validator("storage_dir", mode="after")
    @classmethod
    def _resolve_storage_dir(cls, v: Path) -> Path:
        if not v.is_absolute():
            return (REPO_ROOT / v).resolve()
        return v.resolve()

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    @property
    def templates_dir(self) -> Path:
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

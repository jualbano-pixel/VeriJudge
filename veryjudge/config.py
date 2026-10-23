from pathlib import Path
import os

from pydantic import BaseModel, Field, model_validator
import yaml


class Settings(BaseModel):
    model: str = "stub"
    llm_base_url: str = ""
    provider_keys: dict[str, str] = Field(default_factory=dict)
    max_searches: int = Field(default=8, ge=1, le=8)
    max_captures: int = Field(default=10, ge=1, le=10)
    max_fetch_attempts: int = Field(default=15, ge=1, le=15)
    wall_clock_s: int = Field(default=180, ge=1, le=180)
    OPERATOR_TOKEN: str = Field(min_length=1)
    VIEWER_TOKEN: str = Field(min_length=1)
    database_url: str

    @model_validator(mode="after")
    def distinct_tokens(self):
        if self.OPERATOR_TOKEN == self.VIEWER_TOKEN:
            raise ValueError("OPERATOR_TOKEN and VIEWER_TOKEN must differ")
        return self


def load_settings(path: Path = Path("config.yaml")) -> Settings:
    data = yaml.safe_load(path.read_text())
    for key in ("OPERATOR_TOKEN", "VIEWER_TOKEN"):
        if key in os.environ:
            data[key] = os.environ[key]
    if "DATABASE_URL" in os.environ:
        data["database_url"] = os.environ["DATABASE_URL"]
    if data["database_url"].startswith("postgres://"):
        data["database_url"] = data["database_url"].replace("postgres://", "postgresql+psycopg://", 1)
    elif data["database_url"].startswith("postgresql://"):
        data["database_url"] = data["database_url"].replace("postgresql://", "postgresql+psycopg://", 1)
    if "MODEL" in os.environ:
        data["model"] = os.environ["MODEL"]
    if "LLM_BASE_URL" in os.environ:
        data["llm_base_url"] = os.environ["LLM_BASE_URL"]
    if "BRAVE_SEARCH_API_KEY" in os.environ:
        data.setdefault("provider_keys", {})["web"] = os.environ["BRAVE_SEARCH_API_KEY"]
    if "OPENAI_API_KEY" in os.environ:
        data.setdefault("provider_keys", {})["llm"] = os.environ["OPENAI_API_KEY"]
    return Settings.model_validate(data)

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    # PostgreSQL
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "hospital_assistant"
    db_test_name: str = "hospital_test"
    db_owner_user: str = "hospital_owner"
    db_owner_password: str
    db_app_user: str = "hospital_app"
    db_app_password: str

    # Auth
    jwt_secret: str
    jwt_expire_minutes: int = 120

    # LLM
    llm_provider: str = "groq"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Models
    hf_home: str = "D:/cache/huggingface"
    embedding_model: str = "BAAI/bge-m3"
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # Hospital local time for appointments and slots
    timezone: str = "Asia/Kolkata"

    # Paths
    kb_dir: Path = ROOT_DIR / "data" / "kb"
    seed_dir: Path = ROOT_DIR / "data" / "seed"

    def _url(self, user: str, password: str, database: str) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=user,
            password=password,
            host=self.db_host,
            port=self.db_port,
            database=database,
        )

    @property
    def app_database_url(self) -> URL:
        """Least-privilege role used by the running API."""
        return self._url(self.db_app_user, self.db_app_password, self.db_name)

    @property
    def owner_database_url(self) -> URL:
        """Table owner, used only by setup scripts."""
        return self._url(self.db_owner_user, self.db_owner_password, self.db_name)

    @property
    def test_database_url(self) -> URL:
        return self._url(self.db_owner_user, self.db_owner_password, self.db_test_name)

    @property
    def test_app_database_url(self) -> URL:
        """App role on the test database, for checking least-privilege grants."""
        return self._url(self.db_app_user, self.db_app_password, self.db_test_name)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # HuggingFace reads HF_HOME at import time, so set it before any model library loads.
    os.environ.setdefault("HF_HOME", settings.hf_home)
    return settings

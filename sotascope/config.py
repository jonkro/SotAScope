from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, read from environment variables or .env file."""

    # Root directory for all persistent data (DB, PDFs, cache).
    # Override via SOTASCOPE_DATA_DIR env var.
    data_dir: Path = Path.home() / ".sotascope"

    # OpenAlex API settings
    openalex_api_key: str | None = None
    openalex_base_url: str = "https://api.openalex.org"

    # Crossref API settings (no key required; mailto gets polite pool)
    crossref_base_url: str = "https://api.crossref.org"
    crossref_mailto: str | None = None

    # Crossref fuzzy DOI resolution thresholds
    crossref_resolve_score_threshold: float = 80.0
    crossref_resolve_ratio_threshold: float = 1.5

    @property
    def db_path(self) -> Path:
        new = self.data_dir / "sotascope.db"
        old = self.data_dir / "litexplorer.db"
        if new.exists() or not old.exists():
            return new
        return old

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    model_config = {"env_prefix": "SOTASCOPE_"}


settings = Settings()

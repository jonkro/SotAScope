from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, read from environment variables or .env file."""

    # Root directory for all persistent data (DB, PDFs, cache).
    # Override via LITEXPLORER_DATA_DIR env var.
    data_dir: Path = Path.home() / ".litexplorer"

    # OpenAlex API settings
    openalex_api_key: str | None = None
    openalex_base_url: str = "https://api.openalex.org"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "litexplorer.db"

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    model_config = {"env_prefix": "LITEXPLORER_"}


settings = Settings()

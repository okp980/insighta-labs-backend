from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_client_id: str = Field(...)
    github_client_secret: str = Field(...)
    github_redirect_uri: str = Field(...)
    secret: str = Field(...)

    github_cli_client_id: str = Field(default="")
    github_cli_client_secret: str = Field(default="")
    github_cli_redirect_uri: str = Field(default="http://127.0.0.1:42069/callback")

    frontend_url: str = Field(default="http://localhost:5173")
    cors_allow_origins: str = Field(default="http://localhost:5173")

    cookie_secure: bool = Field(default=False)
    cookie_samesite: str = Field(default="lax")

    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/insighta",
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:5432/dbname",
    )
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)

    profiles_cache_ttl_seconds: int = Field(default=60, ge=1)
    profiles_cache_max_entries: int = Field(default=512, ge=16)

    csv_import_batch_size: int = Field(default=1000, ge=100, le=5000)

    model_config = SettingsConfigDict(env_file=".env")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

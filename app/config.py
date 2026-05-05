from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_client_id: str = Field(...)
    github_client_secret: str = Field(...)
    github_redirect_uri: str = Field(...)
    secret: str = Field(...)

    frontend_url: str = Field(default="http://localhost:5173")
    cors_allow_origins: str = Field(default="http://localhost:5173")

    cookie_secure: bool = Field(default=False)
    cookie_samesite: str = Field(default="lax")

    model_config = SettingsConfigDict(env_file=".env")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

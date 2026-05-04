from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    github_client_id: str = Field(...)
    github_client_secret: str = Field(...)
    github_redirect_uri: str = Field(...)
    secret: str = Field(...)

    model_config = SettingsConfigDict(env_file=".env")

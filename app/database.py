from sqlmodel import SQLModel, create_engine

from .config import Settings

_settings = Settings()
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

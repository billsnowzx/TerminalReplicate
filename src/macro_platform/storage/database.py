from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from macro_platform.config import settings


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or settings.database_url
        connect_args = {"check_same_thread": False} if self.url.startswith("sqlite") else {}
        self.engine = create_engine(self.url, future=True, connect_args=connect_args)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        from macro_platform.storage import tables  # noqa: F401

        Base.metadata.create_all(self.engine)

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

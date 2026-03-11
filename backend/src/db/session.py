"""Database session management with async SQLAlchemy."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .models import Base

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(
    database_url: str = "postgresql+asyncpg://localhost/arbitrage",
) -> AsyncEngine:
    """Create or return the global async engine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            database_url,
            echo=False,
            pool_size=5,
            max_overflow=10,
        )
        logger.info("Database engine created: %s", database_url)
    return _engine


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create or return the global session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


@asynccontextmanager
async def get_session(
    engine: AsyncEngine | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session with automatic commit/rollback."""
    eng = engine or get_engine()
    factory = get_session_factory(eng)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db(
    database_url: str = "postgresql+asyncpg://localhost/arbitrage",
) -> AsyncEngine:
    """Create all tables and return the engine."""
    engine = get_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified.")
    return engine


async def close_db() -> None:
    """Dispose of the engine connection pool."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connection closed.")

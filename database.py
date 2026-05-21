"""
database.py — Database connection and session management

SQLite locally, swap DATABASE_URL for PostgreSQL in production.
"""

import logging
from sqlmodel import SQLModel, create_engine, Session, text
from config import settings

log = logging.getLogger(__name__)


engine = create_engine(
    settings.database_url,
    echo=False,
    connect_args=(
        {"check_same_thread": False}
        if settings.database_url.startswith("sqlite")
        else {}
    ),
)


def init_db() -> None:
    """Create all tables defined in models.py if they don't already exist.

    Also runs simple migrations for added columns (since SQLModel.create_all
    only creates new tables, it doesn't ALTER existing ones).
    """
    SQLModel.metadata.create_all(engine)
    _migrate_add_column("status", "VARCHAR DEFAULT 'published' NOT NULL")
    _migrate_add_column("discarded_at", "DATETIME")


def _migrate_add_column(column_name: str, column_def: str) -> None:
    """Add a column to the signal table if it doesn't already exist."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(signal)"))
            cols = [row[1] for row in result.fetchall()]
            if column_name not in cols:
                conn.execute(text(f"ALTER TABLE signal ADD COLUMN {column_name} {column_def}"))
                conn.commit()
                log.info(f"Migration: added '{column_name}' column to signal table")
            else:
                log.debug(f"Migration: '{column_name}' column already present")
    except Exception as e:
        log.warning(f"Column migration check failed for '{column_name}' (may be normal on first run): {e}")


# Backwards-compat alias so older code paths still work
def _migrate_add_status_column():
    _migrate_add_column("status", "VARCHAR DEFAULT 'published' NOT NULL")


def get_session() -> Session:
    """Hand out a database session. Use as `with get_session() as s: ...`"""
    return Session(engine)

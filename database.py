"""Database engine + session + auto-migration helper"""
import logging
import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./mt5bot.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency — yield a transactional session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def auto_migrate() -> None:
    """
    Auto add missing columns to existing tables (compare model vs DB)
    Lightweight Alembic alternative — supports only ADD COLUMN
    """
    inspector = inspect(engine)
    for table in Base.metadata.tables.values():
        if not inspector.has_table(table.name):
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            col_type = col.type.compile(engine.dialect)
            default = ""
            if col.default is not None and getattr(col.default, "arg", None) is not None:
                d = col.default.arg
                if isinstance(d, str):
                    default = f" DEFAULT '{d}'"
                elif isinstance(d, (int, float, bool)):
                    default = f" DEFAULT {d}"
            stmt = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}{default}'
            logger.info("auto_migrate: %s", stmt)
            with engine.begin() as conn:
                conn.execute(text(stmt))

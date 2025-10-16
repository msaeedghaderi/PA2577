from __future__ import annotations
import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

DB_URL = os.getenv('MT_DB_URL', 'postgresql+psycopg://postgres:postgres@db:5432/postgres')
engine: Engine = create_engine(DB_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

def get_scalar(conn, sql: str, **params):
    return conn.execute(text(sql), params).scalar()

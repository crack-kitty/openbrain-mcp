from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schema" / "001_init.sql"


async def _init_conn(conn: asyncpg.Connection) -> None:
    await register_vector(conn)
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def apply_schema(database_url: str) -> None:
    """Apply schema using a one-shot connection — must run BEFORE pool creation
    because the pool's connection init registers the vector type, which only
    exists after the pgvector extension is created in the schema."""
    if not SCHEMA_PATH.exists():
        raise RuntimeError(f"schema file not found: {SCHEMA_PATH}")
    sql = SCHEMA_PATH.read_text()
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()
    logger.info("schema applied from %s", SCHEMA_PATH)


async def make_pool(database_url: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        database_url, min_size=1, max_size=10, init=_init_conn
    )
    return pool


async def audit(
    pool: asyncpg.Pool,
    *,
    kind: str,
    ref_id: str | None,
    action: str,
    snapshot: dict[str, Any] | None,
    session_id: str | None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO audit_log (kind, ref_id, action, snapshot, session_id)
            VALUES ($1, $2, $3::audit_action, $4, $5)
            """,
            kind,
            ref_id,
            action,
            json.dumps(snapshot) if snapshot is not None else None,
            session_id,
        )

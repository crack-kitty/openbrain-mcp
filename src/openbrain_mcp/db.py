from __future__ import annotations

import json
import logging
import re
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


async def apply_schema(database_url: str, embedding_dimensions: int) -> None:
    """Apply schema using a one-shot connection — must run BEFORE pool creation
    because the pool's connection init registers the vector type, which only
    exists after the pgvector extension is created in the schema.

    The schema uses `vector(__EMBEDDING_DIM__)` as a placeholder; the configured
    embedding dimension is substituted at apply time. If a database already
    exists with a different vector dimension, this function does NOT alter
    existing columns — it raises if the dim doesn't match.
    """
    if not SCHEMA_PATH.exists():
        raise RuntimeError(f"schema file not found: {SCHEMA_PATH}")
    dim = int(embedding_dimensions)
    if dim < 1 or dim > 8192:
        raise RuntimeError(f"embedding_dimensions out of range: {dim}")
    sql = SCHEMA_PATH.read_text().replace("__EMBEDDING_DIM__", str(dim))
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(sql)
        existing_type = await conn.fetchval(
            """
            SELECT format_type(atttypid, atttypmod) FROM pg_attribute
             WHERE attrelid = 'memory_index'::regclass AND attname = 'embedding'
            """
        )
        if existing_type:
            m = re.match(r"vector\((\d+)\)", existing_type)
            if m and int(m.group(1)) != dim:
                raise RuntimeError(
                    f"existing memory_index.embedding type is {existing_type} but "
                    f"OPENBRAIN_EMBEDDING_DIMENSIONS={dim}; either reset the database "
                    f"or set OPENBRAIN_EMBEDDING_DIMENSIONS to match"
                )
    finally:
        await conn.close()
    logger.info("schema applied from %s (vector dim=%d)", SCHEMA_PATH, dim)


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

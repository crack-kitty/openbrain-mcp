"""OpenBrain MCP server — typed-table memory layer for AI agents."""
from __future__ import annotations

import hmac
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import asyncpg
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from .config import Settings, load_settings
from .db import apply_schema, audit, make_pool
from .embeddings import EmbeddingClient
from .write_gate import validate_all

logger = logging.getLogger(__name__)


class Context:
    settings: Settings
    pool: asyncpg.Pool
    embeddings: EmbeddingClient


CTX = Context()


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


_DROP_FIELDS = {"embedding", "headline_tsv"}


def _row_to_dict(row: asyncpg.Record | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = {}
    for k, v in dict(row).items():
        if k in _DROP_FIELDS:
            continue
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif hasattr(v, "tolist"):
            out[k] = v.tolist()
        else:
            out[k] = v
    return out


async def _index_upsert(
    conn: asyncpg.Connection,
    *,
    kind: str,
    ref_id: str,
    headline: str,
    embedding: list[float],
    project: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO memory_index (kind, ref_id, headline, embedding, project)
        VALUES ($1::memory_kind, $2, $3, $4, $5)
        ON CONFLICT (kind, ref_id) DO UPDATE
          SET headline = EXCLUDED.headline,
              embedding = EXCLUDED.embedding,
              project = EXCLUDED.project
        """,
        kind, ref_id, headline, embedding, project,
    )


async def _index_delete(conn: asyncpg.Connection, *, kind: str, ref_id: str) -> None:
    await conn.execute(
        "DELETE FROM memory_index WHERE kind = $1::memory_kind AND ref_id = $2",
        kind, ref_id,
    )


async def _check_duplicate(
    conn: asyncpg.Connection,
    *,
    kind: str,
    embedding: list[float],
    project: str,
    threshold: float,
) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT ref_id, kind::text AS kind, headline,
               1 - (embedding <=> $1) AS similarity
        FROM memory_index
        WHERE project = $2 AND kind = $3::memory_kind
        ORDER BY embedding <=> $1
        LIMIT 1
        """,
        embedding, project, kind,
    )
    if row is None:
        return None
    if float(row["similarity"]) >= threshold:
        return {
            "ref_id": str(row["ref_id"]),
            "kind": row["kind"],
            "headline": row["headline"],
            "similarity": float(row["similarity"]),
        }
    return None


@asynccontextmanager
async def lifespan(server: FastMCP):  # type: ignore[no-untyped-def]
    settings = load_settings()
    CTX.settings = settings
    await apply_schema(settings.database_url, settings.embedding_dimensions)
    CTX.pool = await make_pool(settings.database_url)
    CTX.embeddings = EmbeddingClient(settings)
    logger.info(
        "openbrain ready: provider=%s model=%s dims=%d",
        settings.embedding_provider,
        settings.embedding_model,
        settings.embedding_dimensions,
    )
    try:
        yield
    finally:
        await CTX.embeddings.close()
        await CTX.pool.close()


mcp = FastMCP(
    name="openbrain",
    instructions=(
        "OpenBrain is a typed-table memory layer for AI agents. "
        "Use `boot` first in a new session to load top behavioral rules and active tasks. "
        "Use `capture` to store new memories (rules/facts/incidents/tasks). "
        "Use `search` for hybrid semantic+keyword recall, then `recall` to fetch full bodies. "
        "Memories are scoped by `project` — pass project consistently across calls."
    ),
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------
@mcp.tool()
async def capture(
    kind: str,
    headline: str,
    body: str = "",
    project: str = "default",
    severity: str | None = None,
    tags: list[str] | None = None,
    people: list[str] | None = None,
    topics: list[str] | None = None,
    source: str | None = None,
    priority: int | None = None,
    pinned: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Store a new memory in OpenBrain.

    Use this whenever the user, the assistant, or a workflow surfaces something
    worth remembering across sessions or agents. Pick the right kind:

    - kind='rule'      Behavioral directive ("never push to main", "use uv not pip").
                       Rules are immutable; modify via `supersede`. Requires
                       severity='BLOCKER' (must follow) or 'PATTERN' (should follow).
    - kind='fact'      Knowledge, decision, observation. Decays over time, reactivates
                       on recall. Use for project context, people, topics.
    - kind='incident'  Bug, error, postmortem. Auto-archives after 90 days idle.
    - kind='task'      Action item with status open/blocked/done/stale.

    The write gate enforces: headline ≤15 words, body ≤400 words, type validity.
    Embeddings are generated automatically. Returns the created memory or a
    duplicate-hit warning if cosine similarity exceeds the dedup threshold.

    Always pass `project` to scope memories per-codebase or per-domain.
    """
    settings = CTX.settings
    gate = validate_all(
        kind=kind, headline=headline, body=body, severity=severity, settings=settings
    )
    if not gate.ok:
        return {"ok": False, "error": gate.error}

    embed_text = f"{headline}\n\n{body}".strip()
    embedding = await CTX.embeddings.embed(embed_text)

    async with CTX.pool.acquire() as conn:
        async with conn.transaction():
            dup = await _check_duplicate(
                conn,
                kind=kind,
                embedding=embedding,
                project=project,
                threshold=settings.dedup_threshold,
            )
            if dup is not None:
                return {"ok": False, "duplicate": dup, "hint": "use update or supersede"}

            if kind == "rule":
                row = await conn.fetchrow(
                    """
                    INSERT INTO rules (headline, body, severity, project, tags, embedding, pinned)
                    VALUES ($1, $2, $3::rule_severity, $4, $5, $6, $7)
                    RETURNING *
                    """,
                    headline, body, severity, project, tags or [], embedding, pinned,
                )
            elif kind == "fact":
                row = await conn.fetchrow(
                    """
                    INSERT INTO facts (headline, body, project, source, people, topics, tags, embedding)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING *
                    """,
                    headline, body, project, source,
                    people or [], topics or [], tags or [], embedding,
                )
            elif kind == "incident":
                row = await conn.fetchrow(
                    """
                    INSERT INTO incidents (headline, body, project, severity, tags, embedding)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING *
                    """,
                    headline, body, project, severity, tags or [], embedding,
                )
            else:  # task
                row = await conn.fetchrow(
                    """
                    INSERT INTO tasks (headline, body, project, priority, tags, embedding)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING *
                    """,
                    headline, body, project, priority or 3, tags or [], embedding,
                )

            ref_id = str(row["id"])
            await _index_upsert(
                conn, kind=kind, ref_id=ref_id, headline=headline,
                embedding=embedding, project=project,
            )

    snapshot = _row_to_dict(row)
    await audit(
        CTX.pool, kind=kind, ref_id=ref_id, action="INSERT",
        snapshot=snapshot, session_id=session_id,
    )
    return {"ok": True, "kind": kind, "memory": snapshot}


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
@mcp.tool()
async def search(
    query: str,
    kind: str | None = None,
    project: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Hybrid semantic + keyword search across all memory types.

    Returns a ranked list of headline-only results with similarity scores —
    NOT full bodies. Use this to discover relevant memories, then call `recall`
    with a returned ref_id to fetch the body. Filtering by `kind` narrows to a
    single memory type (rule/fact/incident/task). Filtering by `project` keeps
    results scoped.

    Hybrid weighting: vector similarity (semantic) blended with BM25 (keyword)
    via OPENBRAIN_HYBRID_WEIGHT (default 30% keyword / 70% vector).
    """
    settings = CTX.settings
    embedding = await CTX.embeddings.embed(query)
    weight = settings.hybrid_weight  # keyword weight; vector = 1 - weight
    pool_size = max(limit * 5, 25)

    async with CTX.pool.acquire() as conn:
        vector_rows = await conn.fetch(
            """
            SELECT ref_id, kind::text AS kind, headline, project,
                   1 - (embedding <=> $1) AS score
            FROM memory_index
            WHERE ($2::text IS NULL OR project = $2)
              AND ($3::text IS NULL OR kind = $3::memory_kind)
            ORDER BY embedding <=> $1
            LIMIT $4
            """,
            embedding, project, kind, pool_size,
        )
        keyword_rows = await conn.fetch(
            """
            SELECT ref_id, kind::text AS kind, headline, project,
                   ts_rank_cd(headline_tsv, q) AS score
            FROM memory_index, plainto_tsquery('english', $1) AS q
            WHERE headline_tsv @@ q
              AND ($2::text IS NULL OR project = $2)
              AND ($3::text IS NULL OR kind = $3::memory_kind)
            ORDER BY score DESC
            LIMIT $4
            """,
            query, project, kind, pool_size,
        )

    def _norm(rows: list[asyncpg.Record]) -> dict[tuple[str, str], dict[str, Any]]:
        if not rows:
            return {}
        scores = [float(r["score"]) for r in rows]
        max_s = max(scores) if scores else 1.0
        if max_s <= 0:
            max_s = 1.0
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            key = (str(r["ref_id"]), r["kind"])
            out[key] = {
                "ref_id": str(r["ref_id"]),
                "kind": r["kind"],
                "headline": r["headline"],
                "project": r["project"],
                "score": float(r["score"]) / max_s,
            }
        return out

    v = _norm(vector_rows)
    k_ = _norm(keyword_rows)
    keys = set(v) | set(k_)
    merged: list[dict[str, Any]] = []
    for key in keys:
        v_hit = v.get(key)
        k_hit = k_.get(key)
        base = v_hit or k_hit
        assert base is not None
        v_score = v_hit["score"] if v_hit else 0.0
        k_score = k_hit["score"] if k_hit else 0.0
        hybrid = (1 - weight) * v_score + weight * k_score
        merged.append({
            "ref_id": base["ref_id"],
            "kind": base["kind"],
            "headline": base["headline"],
            "project": base["project"],
            "vector_score": round(v_score, 4),
            "keyword_score": round(k_score, 4),
            "score": round(hybrid, 4),
        })
    merged.sort(key=lambda r: r["score"], reverse=True)
    return {"ok": True, "results": merged[:limit]}


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------
@mcp.tool()
async def recall(
    kind: str,
    ref_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Fetch the full body of a specific memory by kind and ref_id.

    Use this AFTER `search` returns headline-only results — pass the kind and
    ref_id of a hit to get the complete memory. For facts, this also bumps the
    access counter and refreshes the decay score (recall reactivates fading
    memories).
    """
    table_map = {
        "rule": "rules",
        "fact": "facts",
        "incident": "incidents",
        "task": "tasks",
    }
    if kind not in table_map:
        return {"ok": False, "error": f"invalid kind: {kind!r}"}

    async with CTX.pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT * FROM {table_map[kind]} WHERE id = $1", ref_id  # noqa: S608
        )
        if row is None:
            return {"ok": False, "error": "not found"}
        if kind == "fact":
            await conn.execute(
                """
                UPDATE facts
                   SET access_count = access_count + 1,
                       last_accessed_at = now(),
                       decay_score = LEAST(1.0, decay_score + 0.25)
                 WHERE id = $1
                """,
                ref_id,
            )
    return {"ok": True, "kind": kind, "memory": _row_to_dict(row)}


# ---------------------------------------------------------------------------
# boot
# ---------------------------------------------------------------------------
@mcp.tool()
async def boot(
    project: str = "default",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Build a headline-only boot payload for a new session.

    Returns top BLOCKER rules, top PATTERN rules, active tasks, and any
    handoff note from the prior session in this project. Hard-capped at
    OPENBRAIN_BOOT_TOKEN_CAP tokens (default 2000). Truncation order if over
    budget: tasks → patterns → blockers (blockers are most important).

    Call this exactly once per session, ideally first thing. To fetch the
    full body of any returned headline, use `recall` with its kind and ref_id.
    """
    settings = CTX.settings
    async with CTX.pool.acquire() as conn:
        blockers = await conn.fetch(
            """
            SELECT id, headline, project FROM rules
            WHERE severity = 'BLOCKER' AND superseded_by IS NULL
              AND (project = $1 OR project = 'global')
            ORDER BY pinned DESC, created_at DESC
            LIMIT $2
            """,
            project, settings.boot_blocker_cap,
        )
        patterns = await conn.fetch(
            """
            SELECT id, headline, project FROM rules
            WHERE severity = 'PATTERN' AND superseded_by IS NULL
              AND (project = $1 OR project = 'global')
            ORDER BY pinned DESC, created_at DESC
            LIMIT $2
            """,
            project, settings.boot_pattern_cap,
        )
        tasks = await conn.fetch(
            """
            SELECT id, headline, status::text AS status, priority FROM tasks
            WHERE status IN ('open', 'blocked')
              AND (project = $1 OR project = 'global')
            ORDER BY priority ASC, created_at DESC
            LIMIT $2
            """,
            project, settings.boot_task_cap,
        )
        handoff = await conn.fetchrow(
            """
            SELECT session_id, handoff_note, ended_at
            FROM sessions
            WHERE project = $1 AND handoff_note IS NOT NULL
            ORDER BY ended_at DESC NULLS LAST
            LIMIT 1
            """,
            project,
        )

    payload = {
        "blockers": [
            {"ref_id": str(r["id"]), "kind": "rule", "headline": r["headline"]}
            for r in blockers
        ],
        "patterns": [
            {"ref_id": str(r["id"]), "kind": "rule", "headline": r["headline"]}
            for r in patterns
        ],
        "tasks": [
            {
                "ref_id": str(r["id"]),
                "kind": "task",
                "headline": r["headline"],
                "status": r["status"],
                "priority": r["priority"],
            }
            for r in tasks
        ],
        "handoff": _row_to_dict(handoff),
    }

    def _est(p: dict[str, Any]) -> int:
        text = json.dumps(p, default=str)
        return _estimate_tokens(text)

    while _est(payload) > settings.boot_token_cap and payload["tasks"]:
        payload["tasks"].pop()
    while _est(payload) > settings.boot_token_cap and payload["patterns"]:
        payload["patterns"].pop()
    while _est(payload) > settings.boot_token_cap and payload["blockers"]:
        payload["blockers"].pop()

    payload["estimated_tokens"] = _est(payload)
    payload["project"] = project
    payload["ok"] = True
    return payload


# ---------------------------------------------------------------------------
# browse
# ---------------------------------------------------------------------------
@mcp.tool()
async def browse(
    kind: str | None = None,
    project: str | None = None,
    days: int = 7,
    limit: int = 25,
) -> dict[str, Any]:
    """List recent memories with optional filters.

    Useful for "what did I capture this week?" style review. Returns
    headline-only results across kind(s), filtered by project and recency.
    """
    async with CTX.pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ref_id, kind::text AS kind, headline, project, created_at
            FROM memory_index
            WHERE created_at > now() - ($1::int * interval '1 day')
              AND ($2::text IS NULL OR project = $2)
              AND ($3::text IS NULL OR kind = $3::memory_kind)
            ORDER BY created_at DESC
            LIMIT $4
            """,
            days, project, kind, limit,
        )
    return {"ok": True, "results": [_row_to_dict(r) for r in rows]}


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
@mcp.tool()
async def stats(project: str | None = None) -> dict[str, Any]:
    """Aggregate statistics: counts by kind, top topics/people/projects, daily activity.

    Use this to get a high-level snapshot of what's stored. No bodies
    returned. Filter by project to scope, or omit for global stats.
    """
    async with CTX.pool.acquire() as conn:
        kind_counts = await conn.fetch(
            """
            SELECT kind::text AS kind, count(*)::bigint AS n
            FROM memory_index
            WHERE ($1::text IS NULL OR project = $1)
            GROUP BY kind
            """,
            project,
        )
        project_counts = await conn.fetch(
            """
            SELECT project, count(*)::bigint AS n
            FROM memory_index
            WHERE ($1::text IS NULL OR project = $1)
            GROUP BY project
            ORDER BY n DESC
            LIMIT 20
            """,
            project,
        )
        top_topics = await conn.fetch(
            """
            SELECT t AS topic, count(*)::bigint AS n
            FROM facts, unnest(topics) AS t
            WHERE ($1::text IS NULL OR project = $1) AND active
            GROUP BY t ORDER BY n DESC LIMIT 15
            """,
            project,
        )
        top_people = await conn.fetch(
            """
            SELECT p AS person, count(*)::bigint AS n
            FROM facts, unnest(people) AS p
            WHERE ($1::text IS NULL OR project = $1) AND active
            GROUP BY p ORDER BY n DESC LIMIT 15
            """,
            project,
        )
        daily = await conn.fetch(
            """
            SELECT date_trunc('day', created_at)::date AS day, count(*)::bigint AS n
            FROM memory_index
            WHERE created_at > now() - interval '30 days'
              AND ($1::text IS NULL OR project = $1)
            GROUP BY day ORDER BY day
            """,
            project,
        )
    return {
        "ok": True,
        "by_kind": {r["kind"]: r["n"] for r in kind_counts},
        "by_project": [{"project": r["project"], "n": r["n"]} for r in project_counts],
        "top_topics": [{"topic": r["topic"], "n": r["n"]} for r in top_topics],
        "top_people": [{"person": r["person"], "n": r["n"]} for r in top_people],
        "daily_30d": [{"day": r["day"].isoformat(), "n": r["n"]} for r in daily],
    }


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------
@mcp.tool()
async def update(
    kind: str,
    ref_id: str,
    body: str | None = None,
    headline: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    priority: int | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Modify an existing memory's body, headline, tags, or task status.

    For rules, this is BLOCKED — use `supersede` instead so the audit trail
    captures the replacement chain. For tasks, set status='done' to mark
    complete (sets completed_at automatically). Re-embeds the memory if
    headline or body changed.
    """
    if kind == "rule":
        return {
            "ok": False,
            "error": "rules are immutable; use supersede to replace",
        }
    table_map = {"fact": "facts", "incident": "incidents", "task": "tasks"}
    if kind not in table_map:
        return {"ok": False, "error": f"invalid kind: {kind!r}"}
    table = table_map[kind]

    settings = CTX.settings
    sets: list[str] = []
    params: list[Any] = []

    def add(col: str, val: Any, *, cast: str = "") -> None:
        params.append(val)
        sets.append(f"{col} = ${len(params)}{cast}")

    if headline is not None:
        gate = validate_all(
            kind=kind, headline=headline, body=body or "",
            severity=None, settings=settings,
        )
        if not gate.ok:
            return {"ok": False, "error": gate.error}
        add("headline", headline)
    if body is not None:
        gate = validate_all(
            kind=kind, headline=headline or "x", body=body,
            severity=None, settings=settings,
        )
        if not gate.ok:
            return {"ok": False, "error": gate.error}
        add("body", body)
    if tags is not None:
        add("tags", tags)
    if kind == "task":
        if status is not None:
            add("status", status, cast="::task_status")
            if status == "done":
                add("completed_at", datetime.utcnow())
        if priority is not None:
            add("priority", priority)
    add("updated_at", datetime.utcnow())

    if not sets:
        return {"ok": False, "error": "no fields to update"}

    params.append(ref_id)
    sql = f"UPDATE {table} SET {', '.join(sets)} WHERE id = ${len(params)} RETURNING *"  # noqa: S608

    async with CTX.pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(sql, *params)
            if row is None:
                return {"ok": False, "error": "not found"}

            if headline is not None or body is not None:
                embed_text = f"{row['headline']}\n\n{row['body']}".strip()
                embedding = await CTX.embeddings.embed(embed_text)
                await conn.execute(
                    f"UPDATE {table} SET embedding = $1 WHERE id = $2",  # noqa: S608
                    embedding, ref_id,
                )
                await _index_upsert(
                    conn, kind=kind, ref_id=str(row["id"]),
                    headline=row["headline"], embedding=embedding,
                    project=row["project"],
                )

    snapshot = _row_to_dict(row)
    await audit(
        CTX.pool, kind=kind, ref_id=str(row["id"]), action="UPDATE",
        snapshot=snapshot, session_id=session_id,
    )
    return {"ok": True, "kind": kind, "memory": snapshot}


# ---------------------------------------------------------------------------
# supersede
# ---------------------------------------------------------------------------
@mcp.tool()
async def supersede(
    old_ref_id: str,
    headline: str,
    body: str,
    severity: str = "PATTERN",
    project: str = "default",
    tags: list[str] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Replace an existing rule. The old rule is marked DEPRECATED and linked
    to the new one. Use this — never `update` — when behavioral guidance
    changes, so the audit trail preserves the original.
    """
    settings = CTX.settings
    gate = validate_all(
        kind="rule", headline=headline, body=body, severity=severity, settings=settings
    )
    if not gate.ok:
        return {"ok": False, "error": gate.error}

    embedding = await CTX.embeddings.embed(f"{headline}\n\n{body}")

    async with CTX.pool.acquire() as conn:
        async with conn.transaction():
            new = await conn.fetchrow(
                """
                INSERT INTO rules (headline, body, severity, project, tags, embedding)
                VALUES ($1, $2, $3::rule_severity, $4, $5, $6)
                RETURNING *
                """,
                headline, body, severity, project, tags or [], embedding,
            )
            await conn.execute(
                """
                UPDATE rules
                   SET severity = 'DEPRECATED', superseded_by = $1, updated_at = now()
                 WHERE id = $2
                """,
                new["id"], old_ref_id,
            )
            await _index_upsert(
                conn, kind="rule", ref_id=str(new["id"]),
                headline=headline, embedding=embedding, project=project,
            )
            await _index_delete(conn, kind="rule", ref_id=old_ref_id)

    await audit(
        CTX.pool, kind="rule", ref_id=str(new["id"]), action="SUPERSEDE",
        snapshot={"new": _row_to_dict(new), "replaced": old_ref_id},
        session_id=session_id,
    )
    return {"ok": True, "new_rule": _row_to_dict(new), "replaced": old_ref_id}


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------
@mcp.tool()
async def forget(
    kind: str,
    ref_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Soft-delete a memory: facts/tasks → inactive, incidents → archived,
    rules → DEPRECATED. The row stays for audit purposes; the memory_index
    entry is removed so the memory stops appearing in search/boot.
    """
    if kind not in ("rule", "fact", "incident", "task"):
        return {"ok": False, "error": f"invalid kind: {kind!r}"}
    async with CTX.pool.acquire() as conn:
        if kind == "rule":
            await conn.execute(
                "UPDATE rules SET severity = 'DEPRECATED', updated_at = now() WHERE id = $1",
                ref_id,
            )
        elif kind == "fact":
            await conn.execute(
                "UPDATE facts SET active = false, updated_at = now() WHERE id = $1",
                ref_id,
            )
        elif kind == "incident":
            await conn.execute(
                "UPDATE incidents SET archived = true, updated_at = now() WHERE id = $1",
                ref_id,
            )
        else:  # task
            await conn.execute(
                "UPDATE tasks SET status = 'stale'::task_status, updated_at = now() WHERE id = $1",
                ref_id,
            )
        await _index_delete(conn, kind=kind, ref_id=ref_id)
    await audit(
        CTX.pool, kind=kind, ref_id=ref_id, action="DELETE",
        snapshot=None, session_id=session_id,
    )
    return {"ok": True, "kind": kind, "ref_id": ref_id, "soft_deleted": True}


# ---------------------------------------------------------------------------
# session lifecycle
# ---------------------------------------------------------------------------
@mcp.tool()
async def start_session(
    session_id: str,
    source: str = "unknown",
    project: str = "default",
    summary: str | None = None,
) -> dict[str, Any]:
    """Register a new session and return the boot payload + any pending handoff.

    Pass a stable session_id (e.g. UUID or `<source>-<timestamp>`). The boot
    payload returned is the same as calling `boot` directly, but session-aware:
    the handoff_note from the most recent ended session in this project is
    included so context carries forward.
    """
    async with CTX.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (session_id, source, project, summary, active)
            VALUES ($1, $2, $3, $4, true)
            ON CONFLICT (session_id) DO UPDATE
              SET source = EXCLUDED.source,
                  project = EXCLUDED.project,
                  summary = COALESCE(EXCLUDED.summary, sessions.summary),
                  active = true
            """,
            session_id, source, project, summary,
        )
    boot_payload = await boot(project=project, session_id=session_id)
    return {"ok": True, "session_id": session_id, "boot": boot_payload}


@mcp.tool()
async def end_session(
    session_id: str,
    summary: str | None = None,
    handoff_note: str | None = None,
) -> dict[str, Any]:
    """Close a session. Provide a `handoff_note` to leave context for the next
    session in this project — the next `boot` / `start_session` will surface it.
    """
    async with CTX.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE sessions
               SET ended_at = now(),
                   active = false,
                   summary = COALESCE($2, summary),
                   handoff_note = COALESCE($3, handoff_note)
             WHERE session_id = $1
             RETURNING *
            """,
            session_id, summary, handoff_note,
        )
        if row is None:
            return {"ok": False, "error": "session not found"}
    return {"ok": True, "session": _row_to_dict(row)}


# ---------------------------------------------------------------------------
# ASGI app + auth + /health
# ---------------------------------------------------------------------------
class APIKeyAuth(BaseHTTPMiddleware):
    def __init__(self, app, *, access_key: str | None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.access_key = access_key
        self._access_key_bytes = (
            access_key.encode("utf-8") if access_key is not None else None
        )
        if access_key is None:
            logger.warning(
                "OPENBRAIN_MCP_ACCESS_KEY is not set — server is UNAUTHENTICATED. "
                "Set OPENBRAIN_MCP_ACCESS_KEY to a strong random value before exposing publicly."
            )

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path == "/health":
            return await call_next(request)
        if self._access_key_bytes is None:
            return await call_next(request)
        # Bearer token (Claude Code, curl)
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer ") and hmac.compare_digest(
            auth[7:].encode("utf-8"), self._access_key_bytes
        ):
            return await call_next(request)
        # Query parameter ?key=VALUE (claude.ai, ChatGPT, clients that don't support headers)
        key_param = request.query_params.get("key", "")
        if key_param and hmac.compare_digest(
            key_param.encode("utf-8"), self._access_key_bytes
        ):
            return await call_next(request)
        return JSONResponse({"error": "unauthorized"}, status_code=401)


async def _health(_request):  # type: ignore[no-untyped-def]
    return PlainTextResponse("ok")


def build_app() -> Starlette:
    mcp_app = mcp.http_app(transport="streamable-http")

    @asynccontextmanager
    async def combined_lifespan(app):  # type: ignore[no-untyped-def]
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    settings = load_settings()
    app = Starlette(
        lifespan=combined_lifespan,
        routes=[
            Route("/health", _health),
            Mount("/", app=mcp_app),
        ],
    )
    app.add_middleware(APIKeyAuth, access_key=settings.access_key)
    return app

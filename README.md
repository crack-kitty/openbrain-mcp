# openbrain-mcp

Typed-table memory layer for AI agents — Python MCP server backing the OpenBrain Onramp service.

## What it is

A Model Context Protocol (MCP) server that exposes a unified memory store to any MCP-compatible client (Claude Desktop, Claude Code, Cursor, ChatGPT, etc.). Built around the architectural lessons from the shep-engineering OpenBrain v2 redesign:

- **Typed memory tables**: `rules` (immutable behavioral guidance), `facts` (decay-capable knowledge), `incidents` (postmortems), `tasks` (action items) — not a single blob table.
- **Write gate**: every capture is validated for headline/body length, kind validity, and duplicate cosine similarity before insert.
- **Hybrid search**: vector (pgvector HNSW) + keyword (Postgres tsvector + BM25) blended via `OPENBRAIN_HYBRID_WEIGHT`.
- **Headline-only boot payloads**: token-budgeted snapshot for new sessions; full bodies fetched on demand via `recall`.
- **Session handoff**: end one session with a note → next session boot picks it up.
- **Audit log**: every mutation captured.

## MCP tools

`capture`, `search`, `recall`, `boot`, `browse`, `stats`, `update`, `supersede`, `forget`, `start_session`, `end_session`.

## Deployment

This service is intended to run as part of the OpenBrain Onramp service (`/apps/onramp/services-available/openbrain.yml`). The image is published to `ghcr.io/crack-kitty/openbrain-mcp:latest`.

For standalone use:

```bash
docker run --rm -p 8080:8080 \
  -e DATABASE_URL=postgres://openbrain:secret@host:5432/openbrain \
  -e OPENBRAIN_OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OPENBRAIN_MCP_ACCESS_KEY=replace-me \
  ghcr.io/crack-kitty/openbrain-mcp:latest
```

## Configuration

All knobs are environment variables — see `services-scaffold/openbrain/env.template` in the Onramp repo for the full list with defaults.

## Schema

The server applies `schema/001_init.sql` idempotently on every startup. No separate init container.

## License

Private — all rights reserved (for now).

# OpenBrain MCP Server

A self-hosted AI memory layer with typed knowledge tables, hybrid search, and session handoff. Built for [Onramp](https://github.com/traefikturkey/onramp) and any MCP-compatible AI client.

One Postgres database. One MCP endpoint. Every AI tool you use shares the same memory.

---

## Lineage

Inspired by [Nate B. Jones's OB1](https://github.com/NateBJones-Projects/OB1) (Open Brain) and the broader Open Brain community. This is an **independent implementation** — no OB1 code is used. Built from scratch in Python, incorporating architectural lessons from community implementations.

OB1 established the vision: one database, one protocol, every AI tool shares the same memory. This project builds on that vision with a different language, different schema, and additional capabilities learned from real-world deployments.

## What's different from OB1

| | OB1 | OpenBrain MCP |
|---|---|---|
| Language | TypeScript on Deno | Python (FastMCP) |
| Database | Supabase (hosted) | Self-hosted Postgres + pgvector |
| Schema | Single `thoughts` table | Typed tables: rules, facts, incidents, tasks |
| Embeddings | OpenRouter only | Ollama (default), OpenAI, OpenRouter |
| MCP tools | 4 | 11 (adds boot, recall, supersede, session lifecycle, browse, forget, update) |
| Search | Vector cosine | Hybrid (vector + Postgres BM25 keyword) |
| Validation | SHA fingerprint dedup on capture | Write gate: headline/body word limits + semantic-similarity dedup |
| Session tracking | None | `start_session` / `end_session` with handoff notes |
| Memory lifecycle | Manual only | Reactivation tracking on facts (recall bumps the score; periodic decay is scaffolded but disabled by default), soft-delete via `forget`, supersede chain for rules |
| Audit | None | Append-only `audit_log` for every mutation |
| Deployment | Supabase Edge Functions | Docker / Onramp / any container host |

## Features

- **11 MCP tools**: `capture`, `search`, `recall`, `boot`, `browse`, `stats`, `update`, `supersede`, `forget`, `start_session`, `end_session`
- **Typed memory tables** so different memory types can have different lifecycles:
  - `rules` — immutable behavioral guidance, modified only via `supersede`
  - `facts` — knowledge with a reactivation score that recall bumps upward; the periodic decay process is scaffolded (env knobs exposed) but disabled by default in this release
  - `incidents` — postmortems, archivable
  - `tasks` — `open` / `blocked` / `done` / `stale`
- **Write gate** validates every capture: headline ≤15 words, body ≤400 words, semantic-similarity duplicate check (cosine threshold)
- **Hybrid search** blends pgvector cosine similarity with Postgres `tsvector` BM25 — configurable weight
- **Headline-only boot payloads** with hard token cap so a session start doesn't burn 15K tokens loading memory bodies
- **Session handoff** — end one session with a note → next `boot` / `start_session` surfaces it
- **Audit log** — every insert / update / supersede / delete recorded
- **Ollama-first embeddings** — local, free, no API key. OpenAI and OpenRouter supported as alternatives
- **All knobs in `.env`** with sensible defaults

## Quick start — Onramp

If you run [Onramp](https://github.com/traefikturkey/onramp):

```bash
cd /apps/onramp
make enable-service openbrain
make edit-env openbrain          # review auto-generated DB password and access key
make start-service openbrain
make logs openbrain
```

The service is then reachable at `https://openbrain.<HOST_DOMAIN>/mcp/` with TLS via Traefik. The auto-generated `OPENBRAIN_MCP_ACCESS_KEY` in `services-enabled/openbrain.env` is what your MCP clients authenticate with.

You'll also need an embedding model in Ollama:

```bash
docker exec ollama ollama pull nomic-embed-text
```

## Quick start — standalone Docker

```bash
docker network create openbrain
docker run -d --name openbrain-db --network openbrain \
  -e POSTGRES_USER=openbrain \
  -e POSTGRES_PASSWORD=changeme \
  -e POSTGRES_DB=openbrain \
  -v $PWD/pgdata:/var/lib/postgresql/data \
  pgvector/pgvector:pg16

docker run -d --name openbrain --network openbrain -p 8080:8080 \
  -e DATABASE_URL=postgres://openbrain:changeme@openbrain-db:5432/openbrain \
  -e OPENBRAIN_OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OPENBRAIN_MCP_ACCESS_KEY=$(openssl rand -hex 16) \
  ghcr.io/crack-kitty/openbrain-mcp:latest
```

Then point an MCP client at `http://localhost:8080/mcp/` with the bearer token from your run command.

## Connecting AI clients

Every MCP-over-HTTP client needs three things: the URL (`https://your-host/mcp/`), the transport (`http` / `streamable-http`), and the bearer auth header.

### Claude Code

`~/.claude.json`:

```json
{
  "mcpServers": {
    "openbrain": {
      "transport": "http",
      "url": "https://openbrain.example.com/mcp/",
      "headers": {
        "Authorization": "Bearer YOUR_OPENBRAIN_MCP_ACCESS_KEY"
      }
    }
  }
}
```

### Claude.ai (Desktop / Web)

Settings → Connectors → **Add custom connector** → paste the `/mcp/` URL and the bearer token.

### ChatGPT (Plus / Pro)

Settings → Connectors → **Developer Mode** → Add custom MCP server → paste URL and token.

### Cursor / Gemini CLI / Other MCP clients

Anything that speaks MCP over streamable HTTP works. The shape of the config varies — give the client the `/mcp/` URL and the `Authorization: Bearer …` header.

## Configuration

All configuration is via environment variables. Defaults in parentheses.

### Auth & networking
| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | *(required)* | Postgres connection string with pgvector available |
| `OPENBRAIN_HOST` | `0.0.0.0` | Bind address |
| `OPENBRAIN_PORT` | `8080` | Listen port |
| `OPENBRAIN_MCP_ACCESS_KEY` | *(unset → no auth)* | Bearer token clients must pass |

### Embeddings
| Variable | Default | Notes |
|---|---|---|
| `OPENBRAIN_EMBEDDING_PROVIDER` | `ollama` | `ollama` / `openai` / `openrouter` |
| `OPENBRAIN_EMBEDDING_MODEL` | `nomic-embed-text` | Must match the configured provider |
| `OPENBRAIN_EMBEDDING_DIMENSIONS` | `768` | Must match the model's output size |
| `OPENBRAIN_OLLAMA_BASE_URL` | `http://ollama:11434` | Used when provider=ollama |
| `OPENBRAIN_OPENAI_API_KEY` | *(unset)* | Used when provider=openai |
| `OPENBRAIN_OPENAI_MODEL` | `text-embedding-3-small` | OpenAI embedding model |
| `OPENBRAIN_OPENROUTER_API_KEY` | *(unset)* | Used when provider=openrouter |

### Search tuning
| Variable | Default | Notes |
|---|---|---|
| `OPENBRAIN_HYBRID_WEIGHT` | `0.3` | Keyword score weight (0 = pure vector, 1 = pure keyword) |
| `OPENBRAIN_DEDUP_THRESHOLD` | `0.92` | Cosine ≥ this on capture → reject as duplicate |
| `OPENBRAIN_MERGE_LOWER_THRESHOLD` | `0.70` | Lower bound of the smart-merge zone (reserved for future consolidation) |

### Write gate
| Variable | Default | Notes |
|---|---|---|
| `OPENBRAIN_HEADLINE_MAX_WORDS` | `15` | Hard cap on headline length |
| `OPENBRAIN_BODY_MAX_WORDS` | `400` | Hard cap on body length |

### Memory decay (scaffolded — disabled by default)
The fields exist in the schema and the env knobs are wired through `config.py`, but the periodic decay/consolidation process is not yet running in this release. Recall still bumps a fact's `decay_score` upward; nothing currently decreases it.

| Variable | Default | Notes |
|---|---|---|
| `OPENBRAIN_DECAY_LAMBDA` | `0.005` | Reserved: per-day decay rate (consumed only when consolidation is enabled in a future release) |
| `OPENBRAIN_CONSOLIDATION_INTERVAL` | `0` | Reserved: minutes between consolidation passes (0 = disabled; no consumer in this release) |

### Boot payload
| Variable | Default | Notes |
|---|---|---|
| `OPENBRAIN_BOOT_TOKEN_CAP` | `2000` | Hard token cap on the boot payload |
| `OPENBRAIN_BOOT_BLOCKER_CAP` | `5` | Max BLOCKER rules in boot |
| `OPENBRAIN_BOOT_PATTERN_CAP` | `5` | Max PATTERN rules in boot |
| `OPENBRAIN_BOOT_TASK_CAP` | `20` | Max active tasks in boot |

### Optional metadata-extraction LLM
| Variable | Default | Notes |
|---|---|---|
| `OPENBRAIN_METADATA_LLM_PROVIDER` | `ollama` | Reserved for richer auto-tagging on capture |
| `OPENBRAIN_METADATA_LLM_MODEL` | `qwen2.5-coder:14b` | Same |

## Architecture

The schema separates memories by lifecycle, not by content. Behavioral rules and project facts are fundamentally different — they're written for different reasons, accessed at different times, and decay (or don't) at different rates. Stuffing them in one table forced compromises that fell over at scale in earlier Open Brain implementations.

OpenBrain MCP uses four typed tables:

- `rules` — immutable, severity-classified (`BLOCKER` / `PATTERN`), modified only via `supersede` so the audit chain is preserved. Loaded into the boot payload.
- `facts` — knowledge with `access_count` and a `decay_score` that recall bumps upward. The periodic decay process is scaffolded (env knobs exist) but not yet active in this release, so today this is reactivation tracking rather than full decay. Not loaded at boot — fetched on demand via `search` + `recall`.
- `incidents` — postmortems and bug records, archivable after a quiet period.
- `tasks` — open / blocked / done / stale, surfaced in the boot payload while open.

A `memory_index` table mirrors headlines and embeddings across all four kinds for a single search query path. An HNSW index serves cosine search; a `tsvector` GIN index serves keyword search. Hybrid scoring blends the two with a configurable weight.

Every mutation lands in `audit_log` with a JSON snapshot of the row at the time of the change — useful when something looks wrong six months from now. The audit write is **best-effort**: it runs after the main mutation transaction commits, on a separate connection, so a process crash between the commit and the audit insert can leave a committed mutation with no audit row. For a personal memory service this is the right trade-off (the audit is for hindsight, not authorization); harden it if you're storing material that demands a true audit trail.

## Development

```bash
git clone https://github.com/crack-kitty/openbrain-mcp
cd openbrain-mcp
docker build -t openbrain-mcp:dev .
```

Tests run against a real pgvector instance (no mocks) — bring up a temporary container, point `DATABASE_URL` at it, exercise the tools via the `fastmcp` Python client.

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgments

- [Nate B. Jones](https://github.com/NateBJones-Projects) for creating [OB1](https://github.com/NateBJones-Projects/OB1) and the Open Brain concept
- The Open Brain community for architectural patterns and real-world deployment lessons
- Built for the [Onramp](https://github.com/traefikturkey/onramp) self-hosting framework

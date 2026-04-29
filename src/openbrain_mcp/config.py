import os
from dataclasses import dataclass


def _env(key: str, default: str | None = None) -> str | None:
    val = os.environ.get(key)
    if val is None or val == "":
        return default
    return val


def _env_int(key: str, default: int) -> int:
    val = _env(key)
    return int(val) if val is not None else default


def _env_float(key: str, default: float) -> float:
    val = _env(key)
    return float(val) if val is not None else default


@dataclass(frozen=True)
class Settings:
    database_url: str
    host: str
    port: int
    access_key: str | None
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    ollama_base_url: str
    openai_api_key: str | None
    openai_model: str
    openrouter_api_key: str | None
    metadata_llm_provider: str
    metadata_llm_model: str
    hybrid_weight: float
    dedup_threshold: float
    merge_lower_threshold: float
    decay_lambda: float
    consolidation_interval: int
    headline_max_words: int
    body_max_words: int
    boot_token_cap: int
    boot_blocker_cap: int
    boot_pattern_cap: int
    boot_task_cap: int


def load_settings() -> Settings:
    db_url = _env("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is required")
    return Settings(
        database_url=db_url,
        host=_env("OPENBRAIN_HOST", "0.0.0.0"),
        port=_env_int("OPENBRAIN_PORT", 8080),
        access_key=_env("OPENBRAIN_MCP_ACCESS_KEY"),
        embedding_provider=_env("OPENBRAIN_EMBEDDING_PROVIDER", "ollama"),
        embedding_model=_env("OPENBRAIN_EMBEDDING_MODEL", "nomic-embed-text"),
        embedding_dimensions=_env_int("OPENBRAIN_EMBEDDING_DIMENSIONS", 768),
        ollama_base_url=_env("OPENBRAIN_OLLAMA_BASE_URL", "http://ollama:11434"),
        openai_api_key=_env("OPENBRAIN_OPENAI_API_KEY"),
        openai_model=_env("OPENBRAIN_OPENAI_MODEL", "text-embedding-3-small"),
        openrouter_api_key=_env("OPENBRAIN_OPENROUTER_API_KEY"),
        metadata_llm_provider=_env("OPENBRAIN_METADATA_LLM_PROVIDER", "ollama"),
        metadata_llm_model=_env("OPENBRAIN_METADATA_LLM_MODEL", "qwen2.5-coder:14b"),
        hybrid_weight=_env_float("OPENBRAIN_HYBRID_WEIGHT", 0.3),
        dedup_threshold=_env_float("OPENBRAIN_DEDUP_THRESHOLD", 0.92),
        merge_lower_threshold=_env_float("OPENBRAIN_MERGE_LOWER_THRESHOLD", 0.70),
        decay_lambda=_env_float("OPENBRAIN_DECAY_LAMBDA", 0.005),
        consolidation_interval=_env_int("OPENBRAIN_CONSOLIDATION_INTERVAL", 0),
        headline_max_words=_env_int("OPENBRAIN_HEADLINE_MAX_WORDS", 15),
        body_max_words=_env_int("OPENBRAIN_BODY_MAX_WORDS", 400),
        boot_token_cap=_env_int("OPENBRAIN_BOOT_TOKEN_CAP", 2000),
        boot_blocker_cap=_env_int("OPENBRAIN_BOOT_BLOCKER_CAP", 5),
        boot_pattern_cap=_env_int("OPENBRAIN_BOOT_PATTERN_CAP", 5),
        boot_task_cap=_env_int("OPENBRAIN_BOOT_TASK_CAP", 20),
    )

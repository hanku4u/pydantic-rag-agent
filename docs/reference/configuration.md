# Configuration

**Status:** draft — names and defaults agreed; not yet implemented.

How the application is configured, where values come from, and what fails when they
are wrong.

## Rules

1. **Configuration is one Pydantic model** (`config.Settings`), built once at startup
   via `pydantic-settings`.
2. **Secrets come from the environment**, never from a committed file. `.env` is
   ignored; `.env.example` is committed.
3. **No default for credentials.** A missing `OPENROUTER_API_KEY` is an error, not an
   empty string that produces a confusing 401 later.
4. **Every new setting gets a row below** in the same pull request that introduces it.
5. `extra="forbid"`, so a typo'd variable is a startup error rather than a setting
   that silently does nothing.

## Environment variables

All application settings use the `PRAGENT_` prefix to avoid colliding with unrelated
variables in a shared shell. Provider credentials keep their conventional names,
because `pydantic-ai` reads them itself.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | yes | — | Credential for the agent and caption model |
| `VOYAGE_API_KEY` | yes | — | Credential for embeddings. **A separate account from OpenRouter** |
| `PRAGENT_MODEL` | no | `openrouter:deepseek/deepseek-v4.1-flash` | Agent model. Must support tool calling |
| `PRAGENT_CAPTION_MODEL` | no | `PRAGENT_MODEL` | Vision-capable model for captions. The default supports image input |
| `PRAGENT_EMBEDDING_MODEL` | no | `voyageai:voyage-4-large` | Embedder, in `pydantic-ai` provider-string form |
| `PRAGENT_EMBEDDING_DIMENSIONS` | no | `1024` | Output dimension. `voyage-4*` supports 256, 512, 1024, 2048 |
| `PRAGENT_CHROMA_PATH` | no | `./data/chroma` | Persistent Chroma directory |
| `PRAGENT_IMAGE_DIR` | no | `./data/images` | Where extracted images are stored |
| `PRAGENT_CHUNK_SIZE` | no | `800` | Target chunk size in tokens |
| `PRAGENT_CHUNK_OVERLAP` | no | `100` | Token overlap between split chunks |
| `PRAGENT_TOP_K` | no | `5` | Chunks returned per retrieval |
| `PRAGENT_MAX_TOOL_CALLS` | no | `8` | Request limit per turn; bounds the agent loop |
| `PRAGENT_LOG_LEVEL` | no | `INFO` | Log verbosity |
| `PRAGENT_API_URL` | no | `http://127.0.0.1:8000` | Base URL used by the CLI and Streamlit client |

**Two providers, two keys.** OpenRouter runs the agent and captions; VoyageAI supplies
embeddings. OpenRouter serves no embedding models, so this split is forced rather than
chosen — see [ADR 0004](../decisions/0004-model-providers.md). Neither key has a
fallback: an absent one is a startup error.

The prefix in each model string selects the provider (`openrouter:`, `voyageai:`,
`anthropic:`, `ollama:`), so changing provider is a configuration change, not a code
change. The agent slug is **pinned** rather than using OpenRouter's
`~deepseek/deepseek-flash-latest` alias, because a model that changes underneath you
makes evaluation results meaningless. If the pin is retired, change it deliberately.

## Shape

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="PRAGENT_", env_file=".env", extra="forbid"
    )

    model: str = "openrouter:deepseek/deepseek-v4.1-flash"
    caption_model: str | None = None  # falls back to `model`
    embedding_model: str = "voyageai:voyage-4-large"
    embedding_dimensions: int = Field(default=1024, gt=0)
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)
    top_k: int = Field(default=5, gt=0)
    max_tool_calls: int = Field(default=8, gt=0)
```

`OPENROUTER_API_KEY` and `VOYAGE_API_KEY` are intentionally absent: they are the
providers' variables, read by `pydantic-ai`, not ours to duplicate. Their absence still
fails loudly at startup rather than at the first query.

`PRAGENT_EMBEDDING_DIMENSIONS` is not cosmetic. It is written into the index metadata
and compared at startup, because changing it (or the embedding model) invalidates every
stored vector. See [ADR 0003](../decisions/0003-vector-store-and-embeddings.md).

## Secrets in practice

```bash
cp .env.example .env    # fill in both provider keys
```

`.env.example` lists `OPENROUTER_API_KEY=` and `VOYAGE_API_KEY=` with empty values, so
a new contributor can see both are needed without either being committed.

- `.env` and every `.env.*` file are ignored except `.env.example`.
- Never commit a real key, even briefly — git history keeps it and so does anyone who
  fetched.
- Never log settings wholesale. Log model names and paths; redact credentials.
- The two keys belong to **different accounts**. Both live in one `.env`, but
  conflating them produces a 401 that reads like a quota problem.

## Open questions

1. Is a `max_file_size` setting needed, and what happens when it is exceeded —
   reject or truncate?
2. Should the score floor be configurable, and is it per-embedding-model?
3. Is `PRAGENT_MODEL` validated at startup by a cheap probe, or only on first use?
4. Should data paths be absolute-resolved at startup so a `cd` cannot move the index?

## Related

- [Setup](../development/setup.md) — the environment these settings apply to.
- [Ingestion](../design/ingestion.md), [Retrieval](../design/retrieval.md),
  [Agent](../design/agent.md) — what consumes these values.
- [ADR 0003](../decisions/0003-vector-store-and-embeddings.md) — embedding and store choices.

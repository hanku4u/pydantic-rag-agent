# Configuration

**Status:** draft — names and defaults agreed; not yet implemented.

How the application is configured, where values come from, and what fails when they
are wrong.

## Rules

1. **Configuration is one Pydantic model** (`config.Settings`), built once at startup
   via `pydantic-settings`.
2. **Secrets come from the environment**, never from a committed file. `.env` is
   ignored; `.env.example` is committed.
3. **No default for credentials.** A missing `OPENAI_API_KEY` is an error, not an
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
| `OPENAI_API_KEY` | yes | — | Provider credential for the agent and embedder |
| `PRAGENT_MODEL` | yes | — | Model for the agent, e.g. `openai:<model-id>`. Must support tool calling |
| `PRAGENT_CAPTION_MODEL` | no | `PRAGENT_MODEL` | Vision-capable model used to caption images. Must support image input |
| `PRAGENT_EMBEDDING_MODEL` | no | `openai:text-embedding-3-small` | Embedder, in `pydantic-ai` provider-string form |
| `PRAGENT_CHROMA_PATH` | no | `./data/chroma` | Persistent Chroma directory |
| `PRAGENT_IMAGE_DIR` | no | `./data/images` | Where extracted images are stored |
| `PRAGENT_CHUNK_SIZE` | no | `800` | Target chunk size in tokens |
| `PRAGENT_CHUNK_OVERLAP` | no | `100` | Token overlap between split chunks |
| `PRAGENT_TOP_K` | no | `5` | Chunks returned per retrieval |
| `PRAGENT_MAX_TOOL_CALLS` | no | `8` | Request limit per turn; bounds the agent loop |
| `PRAGENT_LOG_LEVEL` | no | `INFO` | Log verbosity |
| `PRAGENT_API_URL` | no | `http://127.0.0.1:8000` | Base URL used by the CLI and Streamlit client |

`PRAGENT_MODEL` has no default on purpose. Model identifiers are retired and renamed
routinely, and a stale default produces a confusing provider error on first run
rather than a clear one. The prefix selects the provider — `openai:`, `anthropic:`,
`ollama:` — so switching providers is a configuration change, not a code change.

## Shape

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="PRAGENT_", env_file=".env", extra="forbid"
    )

    model: str
    caption_model: str | None = None
    embedding_model: str = "openai:text-embedding-3-small"
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)
    top_k: int = Field(default=5, gt=0)
    max_tool_calls: int = Field(default=8, gt=0)
```

`OPENAI_API_KEY` is intentionally absent: it is the provider's variable, read by
`pydantic-ai`, not ours to duplicate. Its absence still fails loudly, at first use or
via an explicit startup check.

## Secrets in practice

```bash
cp .env.example .env    # fill in real values
```

- `.env` and every `.env.*` file are ignored except `.env.example`.
- Never commit a real key, even briefly — git history keeps it and so does anyone who
  fetched.
- Never log settings wholesale. Log model names and paths; redact credentials.

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

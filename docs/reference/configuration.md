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
| `OPENROUTER_API_KEY` | yes | — | One credential for chat, captions, **and** embeddings |
| `PRAGENT_MODEL` | no | `openrouter:deepseek/deepseek-v4.1-flash` | Agent model, as a `pydantic-ai` chat provider string. Must support tool calling |
| `PRAGENT_CAPTION_MODEL` | no | `PRAGENT_MODEL` | Vision-capable model for captions. The default supports image input |
| `PRAGENT_EMBEDDING_MODEL` | no | `qwen/qwen3-embedding-8b` | OpenRouter embedding model id. **Slash, not a colon** — see below |
| `PRAGENT_EMBEDDING_BASE_URL` | no | `https://openrouter.ai/api/v1` | OpenAI-compatible embeddings endpoint |
| `PRAGENT_EMBEDDING_DIMENSIONS` | no | `1024` | Requested output width. The model is natively 4096; 1024 comes from Matryoshka truncation |
| `PRAGENT_CHROMA_PATH` | no | `./data/chroma` | Persistent Chroma directory |
| `PRAGENT_IMAGE_DIR` | no | `./data/images` | Root for extracted image bytes and the caption cache |
| `PRAGENT_CHUNK_SIZE` | no | `800` | Target chunk size in tokens |
| `PRAGENT_CHUNK_OVERLAP` | no | `100` | Token overlap between split chunks |
| `PRAGENT_TOP_K` | no | `5` | Chunks returned per retrieval |
| `PRAGENT_MAX_TOOL_CALLS` | no | `8` | Request limit per turn; bounds the agent loop |
| `PRAGENT_LOG_LEVEL` | no | `INFO` | Log verbosity |
| `PRAGENT_API_URL` | no | `http://127.0.0.1:8000` | Base URL used by the CLI and Streamlit client |

**One vendor, one key.** OpenRouter serves chat, image captioning, and embeddings, so
`OPENROUTER_API_KEY` covers everything — see
[ADR 0004](../decisions/0004-model-providers.md). An absent key is a startup error.

The chat model uses a **provider prefix** (`openrouter:`) because `pydantic-ai` resolves
providers from it. The embedding model does **not**: it is a plain OpenRouter model id
(`qwen/qwen3-embedding-8b`) because the embeddings client is constructed explicitly
against `PRAGENT_EMBEDDING_BASE_URL`. Setting `PRAGENT_EMBEDDING_MODEL` to something
like `openai:text-embedding-3-small` will not do what you expect here.

The agent slug is **pinned** rather than using OpenRouter's
`~deepseek/deepseek-flash-latest` alias, because a model that changes underneath you
makes evaluation results meaningless. If the pin is retired, change it deliberately.

## Shape

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment."""

    model_config = SettingsConfigDict(env_prefix="PRAGENT_", env_file=".env", extra="forbid")

    model: str = "openrouter:deepseek/deepseek-v4.1-flash"
    caption_model: str | None = None  # falls back to `model`
    embedding_model: str = "qwen/qwen3-embedding-8b"
    embedding_base_url: str = "https://openrouter.ai/api/v1"
    embedding_dimensions: int = Field(default=1024, gt=0)
    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)
    top_k: int = Field(default=5, gt=0)
    max_tool_calls: int = Field(default=8, gt=0)
```

`OPENROUTER_API_KEY` is intentionally absent from `Settings`: it is the provider's
variable, read by `pydantic-ai`, not ours to duplicate. Its absence still fails loudly at
startup rather than at the first query. The embeddings client is the exception — we
construct it ourselves, so it reads the same variable explicitly instead of relying on
an SDK default.

`PRAGENT_EMBEDDING_DIMENSIONS` is not cosmetic. It is written into the index metadata
and compared at startup, because changing it (or the embedding model) invalidates every
stored vector. See [ADR 0003](../decisions/0003-vector-store-and-embeddings.md).

It is also why the number must match what the provider actually returns.
Qwen3-Embedding-8B is natively **4096**-wide, but we ask for **1024** via its
Matryoshka training: 4 KiB per chunk vector as float32, so a 10,000-chunk index is
roughly 40 MB before metadata, instead of 160 MB.

That is the model's documented behaviour, not a workaround — but it is only true if
both sides agree. **EMB-1** is responsible for requesting `dimensions=1024`,
truncating and L2-renormalising if OpenRouter ignores the parameter, and failing loudly
when the returned length disagrees. See
[ADR 0005](../decisions/0005-embedding-model-qwen3.md).

## Secrets in practice

```bash
cp .env.example .env    # fill in both provider keys
```

`.env.example` lists `OPENROUTER_API_KEY=` with an empty value, so a new contributor can
see it is required without any real value being committed.

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

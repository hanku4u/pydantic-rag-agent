# 0010 — Split configuration into a tree of settings objects

**Status:** Accepted
**Date:** 2026-10-25

## Context

`config.Settings` is one flat class holding a model id, an API key, an embedding model,
a Chroma path, chunk sizes, retrieval knobs, a history bound and an upload directory. It
was fine while the package was only ever run as one application with one `.env`.

Publication changes that. A library user constructs the agent **in code** and passes what
they have: an existing provider client's credentials, a store they already run, an S3
bucket in the account they already use. A single flat `Settings` makes every one of those
a stringly-typed field on one object, and makes it impossible to configure — or test —
one concern without populating the others.

Three specific problems:

- **No separation of concerns.** The vector store's identity (embedding model and
  dimensions) sits beside the agent's `top_k`, so a mismatch check has to know about both.
- **No partial construction.** Passing an API key in code means overriding a field on a
  class whose other eighteen fields are also environment-shaped.
- **No grouping for documentation.** One table of twenty variables is harder to read than
  six tables of three.

## Decision

Replace the flat class with a tree. Each node is its own plain Pydantic model, and the
root is the only `BaseSettings`:

```text
Settings
├── provider       ProviderSettings        chat + caption model, base URL, API key
├── embeddings     EmbeddingSettings       model, dimensions, base URL, optional key
├── vector_store   VectorStoreSettings     backend, location, collection
├── storage        StorageSettings
│   ├── documents  DocumentStoreSettings   local | s3, bucket, prefix, region
│   └── images     ImageStoreSettings      extracted images and caption cache
├── ingestion      IngestionSettings       chunk size, overlap, captioning on/off
└── agent          AgentSettings           top_k, min_score, budgets, history bounds
```

**Environment mapping.** Keep the `PRAGENT_` prefix and use `env_nested_delimiter="__"`,
so a node is addressed by path: `PRAGENT_VECTOR_STORE__CHROMA_PATH`,
`PRAGENT_STORAGE__DOCUMENTS__BACKEND`, `PRAGENT_AGENT__TOP_K`.

**Programmatic construction is a first-class path.** Every node can be built directly —
`ProviderSettings(api_key=..., model=...)` — and `Settings` accepts the nodes.
`RagAgent.from_env()` is a convenience, not the only door.

**One compatibility allowance.** If `PRAGENT_PROVIDER__API_KEY` is unset, fall back to
`OPENROUTER_API_KEY`, which is what every existing `.env` and every `pydantic-ai` example
in the wild uses. The same applies to the embedding key.

## Migration

Flat names keep working for one release via the mapping below, then are removed at 0.2.0.
The table belongs in [Configuration](../reference/configuration.md), because a migration
nobody can find is a migration that breaks someone's Sunday.

| Old | New |
| --- | --- |
| `PRAGENT_MODEL` | `PRAGENT_PROVIDER__MODEL` |
| `PRAGENT_CAPTION_MODEL` | `PRAGENT_PROVIDER__CAPTION_MODEL` |
| `PRAGENT_EMBEDDING_MODEL` | `PRAGENT_EMBEDDINGS__MODEL` |
| `PRAGENT_EMBEDDING_DIMENSIONS` | `PRAGENT_EMBEDDINGS__DIMENSIONS` |
| `PRAGENT_EMBEDDING_BASE_URL` | `PRAGENT_EMBEDDINGS__BASE_URL` |
| `PRAGENT_CHROMA_PATH` | `PRAGENT_VECTOR_STORE__CHROMA_PATH` |
| `PRAGENT_IMAGE_DIR` | `PRAGENT_STORAGE__IMAGES__DIRECTORY` |
| `PRAGENT_UPLOAD_DIR` | `PRAGENT_STORAGE__DOCUMENTS__LOCAL_PATH` |
| `PRAGENT_CHUNK_SIZE` | `PRAGENT_INGESTION__CHUNK_SIZE` |
| `PRAGENT_CHUNK_OVERLAP` | `PRAGENT_INGESTION__CHUNK_OVERLAP` |
| `PRAGENT_TOP_K` | `PRAGENT_AGENT__TOP_K` |
| `PRAGENT_MIN_SCORE` | `PRAGENT_AGENT__MIN_SCORE` |
| `PRAGENT_MAX_TOOL_CALLS` | `PRAGENT_AGENT__MAX_TOOL_CALLS` |
| `PRAGENT_HISTORY_TURNS` | `PRAGENT_AGENT__HISTORY_TURNS` |
| `PRAGENT_MAX_CONVERSATIONS` | `PRAGENT_AGENT__MAX_CONVERSATIONS` |
| `PRAGENT_LOG_LEVEL` | `PRAGENT_LOG_LEVEL` (unchanged) |

`PRAGENT_API_URL` moves out of `Settings` entirely: it is a client concern, not
something the library needs to know.

## Revision — 2026-10-25, while building CFG-1

Three things the implementation settled, all of them discovered rather than guessed:

1. **The nodes are plain models, not `BaseSettings`.** The first draft made each node a
   settings class with its own prefix and `env_file`. That does not work: a
   `BaseSettings` with `env_file` set reads **every** key in the file, not only those
   matching its prefix, so `ProviderSettings` saw `PRAGENT_EMBEDDING_MODEL` as an extra
   and refused to start. One reader, one file: only the root opens `.env`, and the nodes
   are models the root feeds. The happy side effect is the one CFG-2 needs — a node can
   be constructed in code with no file and no environment at all.
2. **Legacy names are a settings source, not a model validator.** `pydantic-settings`
   discards names that match no field *before* validation, so a flat `PRAGENT_TOP_K`
   never reaches a `mode="before"` validator. A `PydanticBaseSettingsSource` sits below
   that filter, reads the raw `env_vars` that the environment and dotenv sources already
   loaded, and emits the nested structure. It is added last, so every new name outranks
   its old spelling.
3. **`OPENROUTER_API_KEY` needs an explicit alias.** `env_prefix` filters unprefixed
   environment variables, so a root field cannot see the provider's conventional name
   without `validation_alias`. That field is the fallback credential;
   `Settings.require_provider_api_key()` resolves the two names in order and raises with
   both in the message.

One behaviour change came with the move: `PRAGENT_MODEL` used to need the `openrouter:`
prefix. The prefix is now redundant, because the composition root builds an
`OpenAIChatModel` against `PRAGENT_PROVIDER__BASE_URL` explicitly instead of letting a
provider prefix resolve the vendor. Legacy values lose the prefix on the way in.

## Consequences

- **Easier:** construct one concern in a test without a key; document six small tables
  instead of one long one; type a store's identity beside the store it describes.
- **Harder:** more classes, and a nesting convention users must learn. `extra="forbid"`
  stays on every node, so a typo is still a startup error rather than a silent no-op.
- **Foreclosed:** nothing. The flat names survive one release.
- **Revisit when:** a node grows past roughly eight fields, which usually means it is two
  concerns wearing one hat — exactly the diagnosis that produced this record.

## Related

- [Public API](../design/public-api.md) — how a user constructs these in code.
- [Configuration](../reference/configuration.md) — the resulting variable reference.
- [Architecture](../architecture.md) — the composition root that consumes them.

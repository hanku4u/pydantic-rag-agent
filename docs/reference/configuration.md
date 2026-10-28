# Configuration

**Status:** stable — the settings tree (CFG-1) and both document stores (SRC-1, SRC-2)
are implemented.

How the application is configured, where values come from, and what fails when they are
wrong. `pydantic-rag-agent configure` asks for the important ones and writes a `.env`
(merging into an existing file, with a backup); this page is the reference behind it. Decisions behind it: [ADR 0010](../decisions/0010-configuration-tree.md); the
programmatic surface is in [Public API](../design/public-api.md).

## Rules

1. **Configuration is a tree of six nodes** under one root, `config.Settings`. Each node is
   a plain Pydantic model; only the root is a `BaseSettings`, so only the root reads the
   environment and `.env`.
2. **A node can be built in code** — `ProviderSettings(api_key=…)` — without touching the
   environment. That is what makes the library testable and embeddable.
3. **Secrets come from the environment**, never from a committed file. `.env` is ignored;
   `.env.example` is committed.
4. **No default for the provider credential.** A missing key is an error at construction,
   not an empty string that produces a confusing 401 later.
5. **`extra="forbid"` on every node**, so a typo'd variable is a startup error rather than
   a setting that silently does nothing. The root itself ignores unrelated keys, because a
   `.env` regularly holds other tools' variables.
6. **Every new setting gets a row below** in the same pull request that introduces it.

## How names are built

`PRAGENT_` + the node path with `__` between levels + the field name. So
`agent.top_k` is `PRAGENT_AGENT__TOP_K`, and `storage.documents.s3_bucket` is
`PRAGENT_STORAGE__DOCUMENTS__S3_BUCKET`.

`OPENROUTER_API_KEY` keeps its conventional name as a fallback for
`PRAGENT_PROVIDER__API_KEY`, because every `pydantic-ai` example in the wild sets it.

## `provider` — chat and caption model

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PRAGENT_PROVIDER__API_KEY` | yes* | — | Credential for chat, captions, and (by default) embeddings |
| `OPENROUTER_API_KEY` | yes* | — | Conventional fallback for the above |
| `PRAGENT_PROVIDER__MODEL` | no | `deepseek/deepseek-v4.1-flash` | Chat model id. Must support tool calling |
| `PRAGENT_PROVIDER__CAPTION_MODEL` | no | same as `MODEL` | Vision-capable model for image captions |
| `PRAGENT_PROVIDER__BASE_URL` | no | `https://openrouter.ai/api/v1` | Any OpenAI-compatible chat endpoint |

\* One of the two: the root refuses to build without a credential and names both variables
in the error.

The model id is **bare** — `deepseek/deepseek-v4.1-flash`, not
`openrouter:deepseek/deepseek-v4.1-flash`. The endpoint comes from `BASE_URL`, so the
string does not have to carry a vendor prefix and pointing the same code at a local
OpenAI-compatible server is a one-variable change. (Before 0.1.0 the prefix was required;
it is now stripped from legacy values by the migration.)

The agent slug is **pinned** rather than using OpenRouter's
`~deepseek/deepseek-flash-latest` alias, because a model that changes underneath you
makes evaluation results meaningless. If the pin is retired, change it deliberately.

## `embeddings` — index identity

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PRAGENT_EMBEDDINGS__MODEL` | no | `qwen/qwen3-embedding-8b` | Embedding model id. **Slash, not a colon** |
| `PRAGENT_EMBEDDINGS__DIMENSIONS` | no | `1024` | Requested width; written into index metadata |
| `PRAGENT_EMBEDDINGS__BASE_URL` | no | `https://openrouter.ai/api/v1` | OpenAI-compatible embeddings endpoint |
| `PRAGENT_EMBEDDINGS__API_KEY` | no | provider credential | Only needed when embeddings come from a different vendor |

This node is deliberately separate from `provider` even though both default to the same
endpoint: the embedding model and width describe the **index**, not the agent, and the
startup guard compares them against the stored vectors.

`DIMENSIONS` is not cosmetic. It is written into index metadata and compared at startup,
because changing it (or the model) invalidates every stored vector — see
[ADR 0003](../decisions/0003-vector-store-and-embeddings.md).

The number must also match what the provider actually returns. Qwen3-Embedding-8B is
natively **4096**-wide; we ask for **1024** via Matryoshka truncation: 4 KiB per chunk
vector as float32, so a 10,000-chunk index is roughly 40 MB before metadata instead of
160 MB. That is the model's documented behaviour, not a workaround — but it only holds if
both sides agree, so [ADR 0005](../decisions/0005-embedding-model-qwen3.md) and **EMB-1**
make the adapter truncate and L2-renormalise when the provider ignores the parameter, and
fail loudly when the returned length disagrees.

Changing either value is a **rebuild**, not a restart: existing vectors are not comparable,
so the guard refuses to answer from them. There is no `rebuild` command yet, so from the
library:

```python
for document in await agent.documents():
    await agent.delete_document(document.document_id)
await agent.ingest("./docs")
```

From the CLI, `delete` each document id shown by `documents`, then `ingest` again.

## `vector_store` — where vectors live

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PRAGENT_VECTOR_STORE__BACKEND` | no | `chroma` | `chroma` (persistent) or `memory` (process-local) |
| `PRAGENT_VECTOR_STORE__CHROMA_PATH` | no | `./data/chroma` | Persistent Chroma directory |
| `PRAGENT_VECTOR_STORE__COLLECTION` | no | `chunks` | Collection name inside that directory |

`memory` exists for tests, notebooks and one-off questions: nothing survives the process,
so the startup guard has nothing to compare and nothing to mismatch.

## `storage` — originals and derived images

### `storage.documents`

Originals are copied here at ingest (see [ADR 0012](../decisions/0012-source-documents.md)),
so a citation can outlive the request that produced it.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PRAGENT_STORAGE__DOCUMENTS__BACKEND` | no | `local` | `local` (disk) or `s3` (an `[s3]` install) |
| `PRAGENT_STORAGE__DOCUMENTS__LOCAL_PATH` | no | `./data/documents` | Root for the copies taken at ingest, addressed by the catalogue's slug (`<name>`, `<name>-2`) |
| `PRAGENT_STORAGE__DOCUMENTS__S3_BUCKET` | when `backend=s3` | — | Bucket that holds the originals. Required, and refused at startup when missing |
| `PRAGENT_STORAGE__DOCUMENTS__S3_PREFIX` | no | `documents/` | Key prefix inside the bucket. Leading and trailing slashes are normalised |
| `PRAGENT_STORAGE__DOCUMENTS__S3_REGION` | no | SDK default | Region for the S3 client |

### `storage.images`

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PRAGENT_STORAGE__IMAGES__DIRECTORY` | no | `./data/images` | Extracted image bytes and the caption cache |

The caption cache lives under this directory and is keyed by model name, so switching
`CAPTION_MODEL` re-captions rather than silently serving the previous model's text.

## `catalogue` — what the index knows about each document

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PRAGENT_CATALOGUE__URL` | no | `sqlite:///./data/catalogue.db` | Where the document catalogue lives |

One URL, not a backend switch plus per-backend options ([ADR 0018](../decisions/0018-readable-tree-and-catalogue.md)):
the backend is a property of the value, so moving to PostgreSQL is a different URL rather
than a different code path. SQLite is the default so `pip install` and `ingest` still works
with no database server anywhere.

The catalogue is **authoritative**, not a cache. Losing it loses the index — it cannot be
rebuilt by scanning the document tree, because the tree records neither metadata nor chunk
ownership nor captions. Whatever path the URL names is something to back up.

A `postgresql://` URL is accepted by the setting and rejected by the SQLite adapter with an
error naming what is missing, rather than being mistaken for a filename.

## `ingestion` — how documents are split

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PRAGENT_INGESTION__CHUNK_SIZE` | no | `800` | Target chunk size in tokens |
| `PRAGENT_INGESTION__CHUNK_OVERLAP` | no | `100` | Token overlap between chunks; must be smaller than `CHUNK_SIZE` |

Overlap equal to or greater than the chunk size is rejected: the splitter cannot advance,
and a startup error is cheaper than a loop that never terminates.

## `agent` — how it answers

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PRAGENT_AGENT__TOP_K` | no | `5` | Chunks returned per retrieval |
| `PRAGENT_AGENT__MIN_SCORE` | no | — (no floor) | Minimum similarity for a chunk to be returned. **0.59 measured** on the `eval/` corpus, so 0.6 is a safe rounding — see [Evaluation](../development/evaluation.md). Re-measure if the corpus or embedding model changes |
| `PRAGENT_AGENT__MAX_TOOL_CALLS` | no | `8` | Request limit per turn; bounds the agent loop |
| `PRAGENT_AGENT__HISTORY_TURNS` | no | `6` | Recent turns kept per conversation. Trimmed on write, whole turns only — see [ADR 0009](../decisions/0009-conversation-compaction.md) |
| `PRAGENT_AGENT__MAX_CONVERSATIONS` | no | `500` | Conversations retained before the least recently used is evicted |

## What a typo does and does not catch

A misspelled **field** inside a node is a startup error:
`PRAGENT_PROVIDER__MDOEL` fails with `provider.mdoel: Extra inputs are not permitted`.

A misspelled **node** name is not: `PRAGENT_PROVIDER_SETTINGS__MODEL` is an unrelated key
as far as the root is concerned, and is ignored along with the other tools' variables in a
shared `.env`. `pydantic-settings` cannot distinguish the two at that level, and the
alternative — refusing to start because a `.env` holds `AWS_SECRET_ACCESS_KEY` — is worse.
The defaults in the tables above are what you get when a node name is wrong, so check the
effective settings if a variable seems to have no effect.

A **blank** credential counts as absent. `.env.example` ships `OPENROUTER_API_KEY=`, so
copying the file and forgetting to fill it in fails at construction with the variable
names, rather than as a 401 on the first question.

## The root

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `PRAGENT_LOG_LEVEL` | no | `INFO` | Log verbosity |

`PRAGENT_API_URL` is **not** part of `Settings`. It is a client concern: the CLI and
Streamlit read it to decide whether to talk to a running server or build the agent
in-process. See [Interfaces](../design/interfaces.md).

## Migrating from the flat names

The pre-0.1.0 names still work — a settings source folds them into the tree before
validation. **They are removed at 0.2.0.** The new name always wins when both are set, so
migrating one variable at a time is safe.

| Old | New |
| --- | --- |
| `PRAGENT_MODEL` | `PRAGENT_PROVIDER__MODEL` |
| `PRAGENT_CAPTION_MODEL` | `PRAGENT_PROVIDER__CAPTION_MODEL` |
| `OPENROUTER_API_KEY` | `PRAGENT_PROVIDER__API_KEY` (fallback remains) |
| `PRAGENT_EMBEDDING_MODEL` | `PRAGENT_EMBEDDINGS__MODEL` |
| `PRAGENT_EMBEDDING_DIMENSIONS` | `PRAGENT_EMBEDDINGS__DIMENSIONS` |
| `PRAGENT_EMBEDDING_BASE_URL` | `PRAGENT_EMBEDDINGS__BASE_URL` |
| `PRAGENT_CHROMA_PATH` | `PRAGENT_VECTOR_STORE__CHROMA_PATH` |
| `PRAGENT_UPLOAD_DIR` | `PRAGENT_STORAGE__DOCUMENTS__LOCAL_PATH` |
| `PRAGENT_IMAGE_DIR` | `PRAGENT_STORAGE__IMAGES__DIRECTORY` |
| `PRAGENT_CHUNK_SIZE` | `PRAGENT_INGESTION__CHUNK_SIZE` |
| `PRAGENT_CHUNK_OVERLAP` | `PRAGENT_INGESTION__CHUNK_OVERLAP` |
| `PRAGENT_TOP_K` | `PRAGENT_AGENT__TOP_K` |
| `PRAGENT_MIN_SCORE` | `PRAGENT_AGENT__MIN_SCORE` |
| `PRAGENT_MAX_TOOL_CALLS` | `PRAGENT_AGENT__MAX_TOOL_CALLS` |
| `PRAGENT_HISTORY_TURNS` | `PRAGENT_AGENT__HISTORY_TURNS` |
| `PRAGENT_MAX_CONVERSATIONS` | `PRAGENT_AGENT__MAX_CONVERSATIONS` |
| `PRAGENT_LOG_LEVEL` | `PRAGENT_LOG_LEVEL` (unchanged) |

Two behaviour changes come with the move. `PRAGENT_MODEL` used to require the
`openrouter:` prefix; strip it when migrating, as `.env.example` shows. And
`PRAGENT_UPLOAD_DIR` still works but now names the **originals** root rather than an upload
spool: the default moved from `./data/uploads` to `./data/documents`, and files are laid out
`<name>`. An old `.env` that sets it explicitly keeps writing there, so the
old spool directory quietly becomes the permanent store — fine, but worth knowing.

## Shape

The nodes are plain models, so this works with no environment at all:

```python
from pydantic import SecretStr

from pydantic_rag_agent.config import (
    AgentSettings,
    EmbeddingSettings,
    ProviderSettings,
    Settings,
    VectorStoreSettings,
)

settings = Settings(
    provider=ProviderSettings(
        api_key=SecretStr("sk-or-…"),
        model="deepseek/deepseek-v4.1-flash",
    ),
    embeddings=EmbeddingSettings(model="qwen/qwen3-embedding-8b", dimensions=1024),
    vector_store=VectorStoreSettings(backend="memory"),
    agent=AgentSettings(top_k=3),
)
```

Anything omitted keeps its documented default. `Settings()` alone reads `PRAGENT_*` from
the environment and `.env`.

### Why the nodes are not `BaseSettings`

They were, briefly. A settings class with `env_file` set reads **every** key in the file,
not only those matching its `env_prefix`, so a prefixed node treats another node's
variables as extras and refuses to start. Only the root opens the file; the nodes are
plain models that the root feeds. That also made them constructible in code without a
file, which is the library case.

### Why the legacy names are a source, not a validator

`pydantic-settings` discards names that match no field **before** validation runs, so a
flat `PRAGENT_TOP_K` never reaches a model validator. Compatibility has to sit one layer
below the model, where the raw environment is still visible.

## Secrets in practice

```bash
cp .env.example .env    # fill in OPENROUTER_API_KEY
```

`.env.example` lists `OPENROUTER_API_KEY=` with an empty value, so a new contributor can
see it is required without any real value being committed.

- `.env` and every `.env.*` file are ignored except `.env.example`.
- Never commit a real key, even briefly — git history keeps it and so does anyone who
  fetched.
- Never log settings wholesale. `SecretStr` masks credentials in `repr` and tracebacks,
  but a `.get_secret_value()` in a log line defeats it.
- `.env` is read by `pydantic-settings`, not exported into the process environment. The
  composition root therefore builds the model with an explicit provider rather than
  relying on `pydantic-ai`'s environment lookup:

  ```python
  model = OpenAIChatModel(model_id, provider=OpenAIProvider(base_url=…, api_key=…))
  ```

## Open questions

1. **Is a `max_file_size` setting needed?** Yes, eventually — see the same question in
   [ingestion](../design/ingestion.md#open-questions), where it is the sharper problem
   because the upload route buffers the body. Rejecting is the only acceptable behaviour;
   truncating a document would index half of it under a citation that claims the whole.
2. **Should data paths be absolute-resolved at startup so a `cd` cannot move the index?**
   They are not. `vector_store.chroma_path` and `storage.documents.local_path` default to
   `./data/…` and are resolved at use, so a process started from a different directory
   addresses a different — or empty — index. Resolving against the `.env` location at load
   would fix it; nobody has been bitten yet, and the default deployment runs from one
   directory.
3. **Is `PRAGENT_PROVIDER__MODEL` validated at startup by a cheap probe, or only on first
   use?** Only on first use. `check_index()` validates the *embedding* configuration (and
   does so on startup in the standalone app, because a mismatched index returns plausible
   wrong answers), but a bad chat model id surfaces when the first question is asked. A
   probe would spend a request to learn what the first question reveals anyway.
4. ~~**Is the score floor per-embedding-model?**~~ Yes, and it is measured rather than
   guessed: 0.59 for this corpus and embedding model. Re-measure if either changes. See
   [Evaluation](../development/evaluation.md).

## Related

- [Setup](../development/setup.md) — the environment these settings apply to.
- [Public API](../design/public-api.md) — constructing the tree in code.
- [Ingestion](../design/ingestion.md), [Retrieval](../design/retrieval.md),
  [Agent](../design/agent.md) — what consumes these values.
- [ADR 0010](../decisions/0010-configuration-tree.md) — why the tree, and the migration.

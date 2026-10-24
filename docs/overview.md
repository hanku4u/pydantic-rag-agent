# Overview

**Status:** draft — the use case and non-goals are agreed; success metrics are not.

What this project is, who it is for, and what it deliberately does not do.

## What it is

`pydantic-rag-agent` is a retrieval-augmented generation agent for your own
documents. You point it at a pile of files — PDFs, Word documents, slide decks,
plain text — and ask questions about them in a terminal or a browser.

It ingests documents into a vector database, retrieves relevant passages when you
ask something, and lets a `pydantic-ai` agent compose an answer with citations back
to the source. Images are captioned at ingest so they become searchable too.

Every boundary is a Pydantic model: tool arguments, retrieved chunks, API payloads,
configuration. That is the project's organising idea, and the reason for its name.

## Who it is for

- Someone with a folder of documents who wants answers, not a search box.
- A developer who wants a RAG pipeline that is readable and hackable rather than a
  framework they must learn first.

## Goals

- **Ingest the formats people actually have**: PDF, DOCX, PPTX, TXT, Markdown.
- **Make images searchable** via vision-model captions, while keeping the originals
  available for the agent to look at.
- **Cite everything.** An answer without a source is a rumour.
- **Be honest about emptiness.** When nothing relevant is retrieved, say so instead
  of inventing an answer.
- **Swap the storage layer.** Chroma today, `pgvector` when the basics work, behind
  one protocol.
- **Two interfaces, one core**: a terminal CLI and a Streamlit chat UI over a shared
  HTTP API.
- **Deterministic tests.** The suite runs offline; no test spends money.

## Non-goals

- **Not** a hosted or multi-tenant service. Localhost first.
- **Not** a general-purpose agent framework. This is an application.
- **Not** multi-user. One index, one operator, no permissions model in v1.
- **Not** OCR. Scanned PDFs with no text layer are reported as unsupported, not
  faked with a caption.
- **Not** a document editor or a crawler. Files you supply, files it indexes.

## Current state

Accurate as of the planning commit:

| Area | State |
| --- | --- |
| Repository, docs, decision records | Done |
| `uv` project, lockfile, full dependency stack | Done — FND-1 |
| Settings model (`config.Settings`) | Done, startup wiring pending — FND-2 |
| Test harness and offline guard | Done — FND-3 |
| Docs link check in CI | Done — FND-5 |
| CI workflow (lint, types, tests, docs links) | Done — FND-4 |
| Domain models (`domain/`) | Done — DOM-1 |
| Vector store protocol, in-memory and Chroma adapters | Done — STO-1…STO-3 |
| Embeddings: width adaptation, MRL truncation, index guard | Done — EMB-1 |
| Ingestion: text and Markdown, end to end | Done — ING-1…ING-4, ING-8 |
| Ingestion: PDF text and page numbers | Done — ING-5 |
| Ingestion: DOCX — paragraphs, tables, embedded media found | Done — ING-6 |
| Ingestion: PPTX — slides, notes, tables, pictures | Done — ING-7 |
| Ingestion: images — stored, captioned, searchable (DOCX, PPTX, PDF) | Done — IMG-1…IMG-4 |
| Retrieval: query embedding, ranking, score floor, explicit empty results | Done — RET-1…RET-4 |
| Evaluation: recall@5, MRR, floor calibration | Done — EVAL-1 |
| Security: prompt-injection defence (instructions, fencing, no system-prompt injection) | Done — SEC-1 |
| Agent: capability, tools, budgets, multi-turn | Done — AGT-1…AGT-7 |
| API: health, documents, conversations, messages, streaming, error mapping | Done — API-1…API-5 |
| CLI: `serve`, `ingest`, `ask`, `chat`, `documents`, `stats`, `--json` | Done — CLI-1…CLI-3 |
| Streamlit UI: chat, ingest sidebar, citations | Done — UI-1…UI-3 |
| `dev` command: API and UI together | Done — DX-1 |
| Licence | **MIT** — see [LICENSE](../LICENSE) |

The CLI still prints a greeting. [Features](features/README.md) is the ordered plan for
the rest of that table.

## Glossary

| Term | Meaning |
| --- | --- |
| **Document** | A source file, identified by the SHA-256 of its bytes. |
| **Extracted document** | A loaded document as text blocks plus images, before chunking. |
| **Chunk** | A contiguous slice of a document, embedded and retrieved as one unit. |
| **Caption** | A vision-model description of an image, stored as an image chunk. |
| **Embedding** | A dense vector for a chunk, from `qwen/qwen3-embedding-8b` via OpenRouter — 1024 dimensions, Matryoshka-truncated from 4096. |
| **Index** | The persisted collection of chunks and vectors. |
| **Retrieval** | Selecting chunks relevant to a query by vector similarity. |
| **Citation** | The document title, page or slide, and path behind a retrieved chunk. |
| **Agent** | The `pydantic-ai` loop that calls tools and composes the answer. |
| **Tool** | A typed function the agent may call, with a Pydantic-validated schema. |
| **Conversation** | A multi-turn session, identified by `conversation_id`, history held server-side. |

## Related

- [Architecture](architecture.md) — how this becomes modules.
- [Features](features/README.md) — what gets built, in what order.
- [Decision records](decisions/README.md) — the choices behind the stack.

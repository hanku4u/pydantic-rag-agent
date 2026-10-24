# Decision records

**Status:** stable — the convention below is in use.

Architecture Decision Records (ADRs): short documents capturing a decision, the
context that forced it, and what it costs. They exist because the reasoning behind a
choice is invisible in the code that results from it, and because re-litigating
settled questions is one of the most reliable ways to waste a project's time.

They are written for the person who joins in six months — including a coding agent
with no memory of the conversation in which the decision was made.

## When to write one

Write a record when a choice is:

- **Hard to reverse** — a database engine, a package layout, a public interface.
- **Contested** — reasonable people could go the other way.
- **Surprising later** — a workaround, a pinned version, a deliberate limitation.

Do not write one for a dependency bump or a rename. A folder of decisions nobody
reads is worse than no folder, because it trains people to ignore the index.

## Format

| Section | Contains |
| --- | --- |
| **Status** | `Proposed`, `Accepted`, `Superseded by NNNN`, or `Rejected`. |
| **Date** | When the decision was made. |
| **Context** | The forces at play. Neutral, no advocacy. |
| **Decision** | What was chosen, in the active voice. |
| **Consequences** | What becomes easier, what becomes harder, what is now foreclosed. |

A record is immutable once accepted. Change your mind by writing a new one that
supersedes it — editing history to match current opinion destroys the record's only
real value.

## Index

| # | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](0001-project-scaffold.md) | Project scaffold: uv, `src/` layout, Python floor | Accepted | 2026-10-24 |
| [0002](0002-agent-framework.md) | Use pydantic-ai as the agent framework | Accepted | 2026-10-24 |
| [0003](0003-vector-store-and-embeddings.md) | Chroma behind an injectable store interface; OpenAI embeddings | Accepted — embedding half superseded by 0004 | 2026-10-24 |
| [0004](0004-model-providers.md) | All model access through OpenRouter: DeepSeek V4.1 Flash + Voyage embeddings | Accepted | 2026-10-24 |

Next number: **0005**.

## Writing one

Copy [0000-template.md](0000-template.md) to `NNNN-short-slug.md`, use the next
free number, and add a row to the index above.

## Related

- [Architecture](../architecture.md) — the design these records constrain.
- [Conventions](../development/conventions.md) — when a change needs a record.
- [Documentation hub](../README.md) — everything else.

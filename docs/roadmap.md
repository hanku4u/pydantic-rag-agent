# Roadmap

**Status:** stub — milestones are placeholders until the design discussion sets scope.

What gets built, in what order, and what "done" means for each step. Ordered by
**dependency**, not by enthusiasm: each milestone should be usable and testable
before the next begins.

## Guiding rule

Every milestone ends with something you can run and something that proves it
works. A milestone that produces only interfaces and no working path is not a
milestone, it is a bet.

## Milestones

> **Not yet agreed.** The table below is an outline of the *shape* of the work,
> filled in after the design discussion.

| # | Milestone | Done when |
| --- | --- | --- |
| 0 | Project foundation | Repo, docs, CI, and a test harness exist. *Partially done — see below.* |
| 1 | Domain models | Documents, chunks, and queries are modelled, validated, and covered by tests. |
| 2 | Offline retrieval | A corpus can be chunked and searched end to end **without** calling an LLM. |
| 3 | Embeddings + index | Chunks are embedded and persisted; search returns ranked results. |
| 4 | Single-turn answering | A question plus retrieved context produces a cited answer. |
| 5 | Agent loop | The agent chooses tools, including retrieval, and validates their arguments. |
| 6 | Hardening | Error handling, rate limits, cost bounds, and an evaluation set. |

## Foundation status

Honest accounting of milestone 0, so nobody assumes more exists than does:

| Item | State |
| --- | --- |
| Git repository on GitHub (private) | Done |
| uv project, locked dependencies | Done |
| `src/` package and CLI entry point | Done |
| `docs/` and decision records | Done |
| Test framework installed | **No** |
| CI workflow | **No** |
| Licence | **No** |
| Runtime dependencies | **None** |

## Suggested first step

Install the test harness and add one CI workflow that runs
`uv sync --locked` followed by the test command. Doing this before milestone 1 is
cheap; doing it afterwards means the first tests are written against a moving
target. The `--locked` flag is the important part — it fails the build when
`pyproject.toml` and `uv.lock` disagree, which is the usual way a uv project
silently drifts.

## Open questions

1. Is there a deadline or demo that reorders the milestones?
2. Does milestone 5 need to exist at all, or is single-turn retrieval (milestone 4)
   sufficient for the first release?
3. What does "good answer" mean, concretely, and how is it measured? Without this,
   milestone 6 cannot be judged.

## Related

- [Overview](overview.md) — goals and non-goals.
- [Architecture](architecture.md) — the structure the milestones land in.
- [Testing](development/testing.md) — the harness milestone 0 is missing.

# Agent design

**Status:** stub — no decisions made. Sections describe the questions, not answers.

How the agent decides what to do: the control loop, the tools it may call, how it
is prompted, and where it is stopped. Read [Retrieval design](retrieval.md) for the
tool this agent most obviously needs.

## Control loop

The core question is how much machinery sits between a user question and a model
response.

**Open questions**

- Single model call with pre-fetched context, or a real tool-calling loop with
  multiple turns? The first is simpler and often sufficient; the second is what the
  name "agent" implies.
- Maximum iterations, wall-clock budget, or token budget per request? An
  unbounded loop is a billing incident with extra steps.
- On budget exhaustion: return the best partial answer, or fail loudly?
- Is the loop synchronous or `async`?

## Tool surface

Tools are where Pydantic earns its place: an argument schema is validated before
the tool runs, so a malformed call becomes a validation error the model can be told
about, not a stack trace.

**Open questions**

- Which tools exist in the first version? Retrieval alone is defensible; a calculator
  and a file reader are speculative without a use case.
- How is a tool's failure reported to the model — retryable error text, or a raised
  exception that ends the turn? The first is usually more useful and slightly more
  dangerous.
- Are tools a registry, or simply a list passed to the client?
- Do tool calls and results get persisted for debugging? If not, diagnosing a wrong
  answer becomes archaeology.

## Prompting

**Open questions**

- Where do prompts live — inline strings, template files, or a Pydantic model with
  typed slots? Inline strings are the usual default and the usual regret.
- How is retrieved context injected, and with what citation markers?
- What is the instruction when retrieval returns nothing, so the model does not
  confabulate an answer from the question alone?

## Guardrails and observability

**Open questions**

- What is logged per request — prompt, tool calls, token counts, latency, cost? Some
  of this is sensitive; decide which before the first log line.
- Are there prompt-injection defences on retrieved content? Retrieved documents are
  untrusted input and should be treated as data, never as instructions. This is a
  security decision, not a style preference.
- Is there an evaluation set, and what does it measure?

## Related

- [Retrieval design](retrieval.md) — the primary tool.
- [Architecture](../architecture.md) — where the loop lives.
- [Configuration](../reference/configuration.md) — model choice, budgets, iteration caps.
- [Overview](../overview.md) — non-goals, which bound this design.

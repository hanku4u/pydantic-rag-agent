# Configuration

**Status:** stub — no settings exist yet. The rules below are agreed; the variable
names are not.

How the application is configured, where the values come from, and what fails when
they are wrong.

## Rules

These hold regardless of which settings eventually exist:

1. **Configuration is a Pydantic model**, validated once at startup. A missing or
   malformed value fails immediately with a readable message, not three layers deep
   at query time.
2. **Secrets come from the environment**, never from a committed file. `.env` is
   ignored by git; `.env.example` is committed and documents every key with a dummy
   value.
3. **No defaults for credentials.** An absent API key is an error, not an empty
   string that produces a confusing 401 later.
4. **Every new setting gets a row in the table below** in the same pull request that
   introduces it. Undocumented configuration is configuration nobody can deploy.

## Environment variables

> **Not yet agreed.** Populated as settings are introduced.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| _(none yet)_ | | | |

## Expected shape

When the first settings land, they should look roughly like this — a single
validated object, constructed at the edge and passed inward:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="forbid")

    # e.g. embedding_model: str
    # e.g. chunk_size: int = 800
```

`extra="forbid"` is deliberate: a typo'd environment variable should be a startup
error, not a setting that silently does nothing. `pydantic-settings` is not
installed yet — add it with `uv add pydantic-settings` when this is implemented.

## Secrets in practice

```bash
cp .env.example .env    # then fill in real values
```

- `.env` and every `.env.*` file are ignored, except `.env.example`.
- Never commit a real key, even briefly, even in a branch that gets deleted — git
  history keeps it, and so does anyone who fetched.
- Never log configuration wholesale. Log the model names and the resolved paths;
  redact anything that looks like a credential.

## Files and paths

**Open questions**

- Where does the index live on disk — a project-local directory, or platform user
  data directory?
- Is the corpus location configured, or always passed as a CLI argument?
- Are settings namespaced behind a prefix (e.g. `PRAGENT_`) to avoid colliding with
  unrelated variables in a shared environment?

## Related

- [Setup](../development/setup.md) — the environment these settings apply to.
- [Retrieval design](../design/retrieval.md) — chunking and search settings.
- [Agent design](../design/agent.md) — model choice and budget settings.

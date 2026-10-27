# 0018 — A readable document tree with the catalogue as authority (SRC-4)

**Status:** Accepted
**Date:** 2026-10-27

## Context

A stored original is addressed by its content:

```text
file:///…/documents/12/12683a9fa686edba95b52bef422c401ad1f131fe33da96a1191503afc49693fb/handbook.md
```

`document_id` is `sha256(payload)`, and the two-byte prefix exists to keep directory sizes
reasonable. Nothing about that path is wrong — it is stable, deduplicates identical bytes,
and survives a moved source file. It is also unreadable: a person cannot tell from the path
what the document is, cannot predict where a document they are about to ingest will land,
and cannot find one by browsing.

SRC-4 asks for a tree a person can predict from a name, plus a catalogue recording what is
in the index. Its done-when left six questions open, which is why it had not started. The
answers below are now settled, and two of them interact in a way that changes the feature.

## Decision

### 1. A slug, not the source path

Originals land at a path derived from the document's own name:

```text
documents/handbook.md
documents/2026-budget.xlsx
documents/handbook-2.md
```

The relative path under the collection root is **not** preserved. Two `handbook.md` from
different folders is the case that decides this: preserving the path means the tree mirrors
whatever directory structure the caller happened to have, which is information the index
does not otherwise use and which changes when someone reorganises their files. A slug
derived from the name is stable against that.

`document_id` **stays** `sha256(payload)`. It identifies content, it is embedded in chunk
metadata, it appears in citation URLs, and it is the key SRC-1…SRC-3 and CIT-1 already
store and serve. Changing it to a slug would be a breaking change to every locator in every
existing index for a naming improvement. The slug is a *path component*; the id is the
*identity*.

Collisions are resolved by the catalogue, which is the only component that can see all
documents at once. The first claimant keeps the bare slug; later ones get a numeric suffix.
The suffix is a decision of the catalogue, not of the bytes, which is precisely why this
pairs with decision 2.

### 2. The catalogue is the source of truth

The catalogue is authority, **not** a derived index rebuildable from the tree.

This is the load-bearing decision of SRC-4, and it has consequences that must be stated
rather than discovered:

- **The database is now critical data.** Losing it loses the index. It is not recoverable by
  scanning the tree, because the tree does not record which documents were ingested under
  which metadata, which chunks belong to which document, or what the caption ids are. It
  goes under `./data/` (gitignored) and SRC-4 documents a backup as part of the feature.
- **Slug uniqueness lives in the catalogue.** The suffix in `handbook-2.md` is a row, not a
  filesystem fact. This is why decision 1 is only safe together with decision 2.
- **"Re-ingest to rebuild" replaces "rebuild from the tree".** A user who deletes the
  database gets an empty index and re-ingests their sources. That must be the documented
  recovery path, not a data-corruption surprise.
- **Schema migrations own the truth.** The catalogue's schema is the thing that must keep
  working across upgrades, which is why decision 4 picks a driver and a migration story
  together.

The alternative — rebuildable from the tree — was rejected because it cannot represent
caller-supplied metadata (SRC-5) or caption text (decision 3) without writing that data
into the tree as sidecar files, which is a second undocumented format to keep in sync.

### 3. The catalogue owns caption text

Caption text and its metadata move into the catalogue. Image *bytes* stay
content-addressed on disk.

The reason is that a caption is a property of a document, and SRC-4 already says the
catalogue records attributes of documents. Leaving captions as loose cache files means two
places hold document metadata and they can disagree.

The existing cache behaviour is preserved rather than replaced: the catalogue row is keyed on
`(image_hash, caption_model)`, so **IMG-5 still holds** — changing the caption model misses
the cache and re-captions instead of silently serving the old model's descriptions. The
on-disk cache becomes a read-through layer in front of the catalogue, or is removed; that is
an implementation choice for the change, not an architectural one.

Deduplication is preserved by keeping bytes content-addressed: the same logo on forty slides
is one file and forty catalogue rows pointing at it.

### 4. A single `DATABASE_URL`

One setting, one driver, one URL:

```dotenv
PRAGENT_CATALOGUE__URL=sqlite:///./data/catalogue.db     # default
PRAGENT_CATALOGUE__URL=postgresql://user:pw@host/db      # when configured
```

Not a new settings node per backend plus separate `psycopg`/`asyncpg` extras. One URL means
the backend is a property of the value rather than of the code path, and it matches how
every other tool in this space is configured.

SQLite is the default so the zero-configuration story survives: `pip install` plus `ingest`
still works with no database server. PostgreSQL is opt-in through the URL, with its driver
behind an extra so the base install stays lean — the same rule the release gates now
enforce for every other extra.

Migrations are **plain SQL, versioned, applied on first open**, with the schema version
recorded in the database. Alembic was considered and rejected: it brings a configuration
file, an environment module and a review ritual for a schema that is a handful of tables.
The trade is that migrations must be written by hand and must be forward-only; a downgrade
story is not offered, because the recovery path for a bad migration is the backup that
decision 2 already makes mandatory.

`sqlite3` and the PostgreSQL driver must present the same query surface to the catalogue
adapter, which is the real cost of a single URL and belongs in the adapter, not in the
application layer.

### 5. Migration is a reindex, stated plainly

Existing locators live in chunk metadata. SRC-4 changes the stored path, so existing
indexes cannot simply be read under the new scheme.

**A reindex is required, and the feature must say so before anyone upgrades.** No in-place
path rewrite: the old tree is content-addressed, the new one is slug-addressed, and an
automatic rewrite would have to invent slugs for documents whose catalogue rows do not exist
yet — which is exactly the state a pre-SRC-4 index is in.

The upgrade path, documented in the changelog and the migration note:

1. Upgrade the package.
2. Re-ingest your sources. The catalogue is created, slugs are assigned, originals are
   rewritten into the readable tree.
3. The old content-addressed tree is left in place and can be deleted.

A backfill command is offered only if it can be written without guessing: it can, because
the sources are the input, so the "backfill" *is* re-ingest and does not need a separate
code path.

### 6. Authentication is not this package's job

SRC-4 adds a route surface and a readable tree, and the API still has no authentication.
That is now a **documented boundary rather than a deferred feature**: this package does not
implement auth, and does not claim to.

The README already carries the warning for the download route; SRC-4 extends it to the
catalogue and tree routes. The reasoning is that authentication is a deployment concern with
a different shape in every host — an OAuth proxy, a reverse proxy with mTLS, an API gateway,
a VPN. A package that guesses gets a half-answer that hosts disable. The Deferred table's
"deployment beyond localhost" trigger is therefore removed for auth and replaced by this
boundary, so it stops reading as a promise.

### 7. The model chooses the filter (RET-5)

For RET-5, the model selects the metadata filter from the question, rather than requiring a
deterministic caller-supplied filter per query.

```bash
pydantic-rag-agent ask "what did finance say about Q3?"   # model narrows to department=finance
```

Accepted with a known failure mode: a model choosing from the question will sometimes choose
wrong, and a wrong filter excludes correct evidence — the failure is silent, because the
answer simply omits what it could not see. The deterministic form
(`ask --meta department=finance`) still exists as an explicit override and is what a caller
uses when correctness matters more than convenience.

This is a deliberate start-simple choice, revisitable: if evaluation shows the model choosing
badly, the filter moves to caller-only. RET-5's done-when already requires a test proving a
semantically matching document is excluded when the filter says so, which is the test that
makes the failure mode visible rather than latent.

## Consequences

- **The database becomes the thing to back up.** This is the largest change in operational
  posture since SRC-1, and the feature is incomplete without it documented.
- **A broken slug collision policy corrupts documents rather than failing.** Two documents
  resolving to one path is silent data loss, so the collision test is not optional.
- **Reindex is mandatory on upgrade.** Anyone with an existing index must re-ingest, and
  must be told before they upgrade rather than after their citations 404.
- **The tree is browsable but no longer deduplicated by path.** Identical bytes uploaded
  under two names are two files, because a readable tree names documents, not hashes. The
  catalogue still knows both share a `document_id`, so the deduplication is available to
  anything that asks the catalogue.
- **No auth, stated.** Anyone who can reach the port can read the catalogue and the tree.
  This is now a boundary, not a TODO.

## Related

- [SRC-4, SRC-5, RET-5](../features/README.md) — the done-when criteria these decisions unblock
- [ADR 0012](0012-source-documents.md) — originals and locators, which this changes
- [ADR 0017](0017-citation-download.md) — the download route, whose auth warning this extends
- [ADR 0010](0010-configuration-tree.md) — the settings tree the catalogue node joins

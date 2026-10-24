"""Shared test fixtures.

Two invariants hold for the whole suite, and both are enforced here rather than
left to individual test authors:

1. **No network model calls.** ``ALLOW_MODEL_REQUESTS`` is switched off, so a
   test that forgets to override a model fails loudly instead of quietly
   spending money against a paid API.
2. **No ambient configuration.** The developer's own ``PRAGENT_*`` variables and
   any local ``.env`` are cleared, so a passing suite on one machine means the
   same thing on another.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pydantic_ai.models
import pytest

pydantic_ai.models.ALLOW_MODEL_REQUESTS = False

_SETTINGS_ENV_PREFIX = "PRAGENT_"
_PROVIDER_ENV_VARS = ("OPENROUTER_API_KEY",)


@pytest.fixture
def clean_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear every settings-related variable, then supply a dummy credential.

    Tests build ``Settings(_env_file=None)`` so a local ``.env`` cannot leak in
    either; the dummy key exists because its absence is itself under test.
    """
    for name in list(os.environ):
        if name.startswith(_SETTINGS_ENV_PREFIX) or name in _PROVIDER_ENV_VARS:
            monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-a-real-credential")
    yield

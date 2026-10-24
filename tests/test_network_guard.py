"""Guards the guard.

``conftest.py`` disables model requests for the whole suite. If that ever stops
working, every future test quietly becomes capable of spending real money against
a paid API — a failure mode that produces no error, only an invoice. So the
behaviour is asserted here rather than assumed.
"""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai import models as pydantic_ai_models


def network_guard_with_live_model_in_test_session_should_fail_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: a credential is present, so nothing fails for the mundane reason
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-key-for-guard-test")

    # and: the suite-wide guard is in place
    assert pydantic_ai_models.ALLOW_MODEL_REQUESTS is False

    # when: a test forgets to override the model and issues a real run
    agent = Agent("openrouter:deepseek/deepseek-v4.1-flash")

    # then: it is refused locally, with no network call and therefore no charge
    with pytest.raises(RuntimeError, match="ALLOW_MODEL_REQUESTS is False"):
        agent.run_sync("this must never reach the provider")

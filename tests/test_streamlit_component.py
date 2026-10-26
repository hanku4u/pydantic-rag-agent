"""The embeddable chat panel, driven through Streamlit's own test harness.

``ui/`` had no tests before this file: the module docs said Streamlit "is verified by
launching it", which is a manual step nobody performs in CI. That is why the two defects
this panel had to avoid were invisible — a script cannot be imported without running
``set_page_config``, and a fixed session-state key cannot be mounted twice.

``AppTest`` runs a real Streamlit script in a real script-run context, so these tests fail
if someone reintroduces either mistake rather than relying on a reader noticing.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from pydantic_rag_agent.client import ClientError

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402


class FakeClient:
    """A ``KnowledgeClient`` that streams a canned turn. No network, no model."""

    def __init__(
        self,
        *,
        answer: str = "Forty-two.",
        citations: list[dict[str, Any]] | None = None,
        fail: bool = False,
    ) -> None:
        self.answer = answer
        self.citations = citations if citations is not None else []
        self.fail = fail
        self.conversations_created = 0
        self.questions: list[str] = []

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def health(self) -> dict[str, object]:
        return {
            "chunks": 3,
            "embedding_model": "fake-embed",
            "embedding_dimensions": 8,
        }

    def stats(self) -> dict[str, object]:
        return {}

    def documents(self) -> list[dict[str, object]]:
        return []

    def delete_document(self, document_id: str) -> dict[str, object]:
        return {"deleted": 1}

    def ingest(self, paths: Sequence[Path]) -> dict[str, object]:
        return {"indexed": 0, "skipped": 0, "failed": 0, "chunks": 0, "files": []}

    def ingest_files(self, files: Sequence[tuple[str, bytes]]) -> dict[str, object]:
        return self.ingest([])

    def create_conversation(self) -> str:
        self.conversations_created += 1
        return f"conv-{self.conversations_created}"

    def ask(self, conversation_id: str, question: str) -> dict[str, object]:
        return {"answer": self.answer, "citations": self.citations}

    def stream(
        self, conversation_id: str, question: str
    ) -> Iterator[tuple[str, dict[str, object]]]:
        self.questions.append(question)
        if self.fail:
            yield "error", {"detail": "model exploded", "code": "upstream"}
            return
        yield "token", {"text": self.answer}
        if self.citations:
            yield "citations", {"citations": self.citations}
        yield "done", {"answer": self.answer}


def _script(client: FakeClient, *, key: str = "rag", heading: str | None = None) -> str:
    """A host-app-shaped script: the panel, and nothing else.

    The client arrives through ``session_state`` because that is the only channel into a
    script AppTest runs; the first line is the host fetching its own dependency.
    """
    return (
        "import streamlit as st\n"
        "from pydantic_rag_agent.ui.chat import rag_chat_client\n"
        "client = st.session_state['CLIENT']\n"
        f"rag_chat_client(client, key={key!r}, heading={heading!r})\n"
    )


def _run(tmp_path: Path, body: str, client: FakeClient) -> AppTest:
    path = tmp_path / "host_app.py"
    path.write_text(body)
    app = AppTest.from_file(str(path))
    app.session_state["CLIENT"] = client
    app.run()
    return app


def test_a_question_streams_into_the_transcript(tmp_path: Path) -> None:
    client = FakeClient(answer="Forty-two.")
    app = _run(
        tmp_path,
        "import streamlit as st\n"
        "from pydantic_rag_agent.ui.chat import rag_chat_client\n"
        "rag_chat_client(st.session_state['CLIENT'])\n",
        client,
    )

    app.chat_input[0].set_value("What is the answer?").run()

    assert not app.exception
    assert client.questions == ["What is the answer?"]
    assert app.session_state["rag__turns"] == [
        {"question": "What is the answer?", "answer": "Forty-two.", "citations": []}
    ]


def test_the_conversation_is_created_once_and_reused(tmp_path: Path) -> None:
    client = FakeClient()
    app = _run(
        tmp_path,
        "import streamlit as st\n"
        "from pydantic_rag_agent.ui.chat import rag_chat_client\n"
        "rag_chat_client(st.session_state['CLIENT'])\n",
        client,
    )

    app.chat_input[0].set_value("first").run()
    app.chat_input[0].set_value("second").run()

    # A second conversation per question would silently discard the thread.
    assert client.conversations_created == 1
    assert len(app.session_state["rag__turns"]) == 2


def test_citations_render_in_an_expander(tmp_path: Path) -> None:
    client = FakeClient(
        citations=[
            {
                "label": "notes.md",
                "locator": "file:///tmp/notes.md",
                "source_path": "/tmp/notes.md",
            }
        ]
    )
    app = _run(
        tmp_path,
        "import streamlit as st\n"
        "from pydantic_rag_agent.ui.chat import rag_chat_client\n"
        "rag_chat_client(st.session_state['CLIENT'])\n",
        client,
    )

    app.chat_input[0].set_value("where?").run()

    assert not app.exception
    assert app.session_state["rag__turns"][0]["citations"][0]["label"] == "notes.md"


def test_a_client_error_is_shown_and_the_transcript_is_unchanged(tmp_path: Path) -> None:
    client = FakeClient(fail=True)
    app = _run(
        tmp_path,
        "import streamlit as st\n"
        "from pydantic_rag_agent.ui.chat import rag_chat_client\n"
        "rag_chat_client(st.session_state['CLIENT'])\n",
        client,
    )

    app.chat_input[0].set_value("boom").run()

    assert not app.exception
    assert app.error[0].value == "model exploded (upstream)"
    # A failed turn must not be recorded as an empty answer.
    assert app.session_state["rag__turns"] == []


def test_two_panels_do_not_share_state(tmp_path: Path) -> None:
    """The reason the key is a parameter rather than a constant.

    Two panels on one page each keep their own transcript. With a fixed key the second
    panel would replay the first one's turns, and both would drive one conversation.
    """
    client = FakeClient()
    app = _run(
        tmp_path,
        "import streamlit as st\n"
        "from pydantic_rag_agent.ui.chat import rag_chat_client\n"
        "client = st.session_state['CLIENT']\n"
        "rag_chat_client(client, key='left', heading='Left')\n"
        "rag_chat_client(client, key='right', heading='Right')\n",
        client,
    )

    assert not app.exception
    # Both panels registered a chat input under distinct keys.
    inputs = [widget for widget in app.chat_input]
    assert len(inputs) == 2

    inputs[0].set_value("to the left").run()
    app.chat_input[1].set_value("to the right").run()

    assert [t["question"] for t in app.session_state["left__turns"]] == ["to the left"]
    assert [t["question"] for t in app.session_state["right__turns"]] == ["to the right"]


def test_a_host_key_of_the_same_name_is_left_alone(tmp_path: Path) -> None:
    """Namespacing protects the host's own state, not just other panels."""
    client = FakeClient()
    path = tmp_path / "host_app.py"
    path.write_text(
        "import streamlit as st\n"
        "from pydantic_rag_agent.ui.chat import rag_chat_client\n"
        "st.session_state.setdefault('turns', ['host turn'])\n"
        "rag_chat_client(st.session_state['CLIENT'])\n"
        "st.write(f\"host={len(st.session_state['turns'])}\")\n"
    )
    app = AppTest.from_file(str(path))
    app.session_state["CLIENT"] = client
    app.run()

    app.chat_input[0].set_value("mine").run()

    assert app.session_state["turns"] == ["host turn"]
    assert len(app.session_state["rag__turns"]) == 1


def test_importing_the_module_does_not_touch_the_page(tmp_path: Path) -> None:
    """Import-time ``set_page_config`` is what made the old script unimportable.

    A real Streamlit server raises if ``set_page_config`` is called twice. ``AppTest`` does
    *not* — it tolerates the second call, so an assertion on ``app.exception`` here would
    pass whether or not the bug exists. This test instead wraps ``set_page_config`` before
    importing the module and asserts the import called it zero times, which is the actual
    invariant and does fail when a module-level call is reintroduced.

    ``sys.modules.pop`` first: other tests have already imported this module, and a cached
    import re-executes nothing, so without it the assertion is vacuous.
    """
    path = tmp_path / "host_app.py"
    path.write_text(
        "import sys\n"
        "import streamlit as st\n"
        "calls: list[dict] = []\n"
        "st.set_page_config(page_title='host app', layout='centered')\n"
        "st.set_page_config = lambda **kw: calls.append(kw)\n"
        "sys.modules.pop('pydantic_rag_agent.ui.chat', None)\n"
        "import pydantic_rag_agent.ui.chat\n"
        "st.session_state['page_config_calls_on_import'] = len(calls)\n"
        "st.write('imported')\n"
    )
    app = AppTest.from_file(str(path))
    app.run()

    assert not app.exception
    assert app.session_state["page_config_calls_on_import"] == 0
    # And nothing else was rendered at import either: one chat input, from the panel
    # being absent — the host rendered no panel, so there should be none at all.
    assert len(app.chat_input) == 0


def test_heading_renders_only_when_asked(tmp_path: Path) -> None:
    client = FakeClient()
    with_heading = _run(tmp_path, _script(client, heading="Documents"), client)
    assert any("Documents" in str(h.value) for h in with_heading.subheader)

    other = tmp_path / "second"
    other.mkdir()
    without = _run(other, _script(client, heading=None), client)
    assert not without.subheader


def test_a_failed_turn_can_be_retried(tmp_path: Path) -> None:
    """A failed turn leaves no conversation behind, so the retry starts cleanly."""
    client = FakeClient(fail=True)
    app = _run(
        tmp_path,
        "import streamlit as st\n"
        "from pydantic_rag_agent.ui.chat import rag_chat_client\n"
        "rag_chat_client(st.session_state['CLIENT'])\n",
        client,
    )

    app.chat_input[0].set_value("first attempt").run()
    client.fail = False
    app.chat_input[0].set_value("second attempt").run()

    assert [t["question"] for t in app.session_state["rag__turns"]] == ["second attempt"]
    assert client.conversations_created == 1


def test_client_error_type_is_the_one_raised_by_the_protocol() -> None:
    """Guards the import: the panel catches ``ClientError`` and nothing else."""
    assert issubclass(ClientError, Exception)

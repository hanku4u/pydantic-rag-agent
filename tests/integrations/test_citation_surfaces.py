"""Citations as downloads, in both interfaces, with the fallback each one promises.

CIT-1 says two things that are easy to fake: in Streamlit a citation downloads the original,
and in the CLI it is a hyperlink *where the terminal supports one, with the locator printed
as fallback*. A test that only checks "a link appears" would pass while leaving a user with
a terminal that does not support links holding nothing.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from pydantic_rag_agent.cli import _render_citations

CITATIONS: list[dict[str, Any]] = [
    {
        "label": "Handbook > Backups, p. 4",
        "document_id": "26ec2905" + "0" * 56,
        "locator": "file:///data/documents/26/26ec2905/handbook.md",
        "source_path": "handbook.md",
    },
    {
        "label": "Pricing",
        "document_id": "aa" + "0" * 62,
        "locator": "s3://acme-docs/documents/aa/aa/pricing.pdf",
        "source_path": "pricing.pdf",
    },
]


def _render(*, terminal: bool) -> str:
    buffer = io.StringIO()
    console = Console(file=buffer, width=100, force_terminal=terminal, no_color=not terminal)
    _render_citations(console, CITATIONS)
    return buffer.getvalue()


def test_a_terminal_gets_a_clickable_link() -> None:
    """OSC 8, the escape a terminal uses to make text clickable.

    Asserted on the escape rather than on "the address is present", because the address is
    present either way — that is the fallback, not the feature.
    """
    output = _render(terminal=True)

    assert "\x1b]8;;" in output
    assert "file:///data/documents" in output


def test_the_locator_is_still_printed_when_the_terminal_cannot_link() -> None:
    """The fallback the feature promises: piped output has no escapes, and every address."""
    output = _render(terminal=False)

    assert "\x1b]8;;" not in output
    assert "file:///data/documents/26/26ec2905/handbook.md" in output
    assert "s3://acme-docs/documents/aa/aa/pricing.pdf" in output


def test_an_s3_address_is_not_turned_into_a_link() -> None:
    """No terminal has an `s3://` handler, so a link there goes nowhere.

    A dead link is worse than a copyable address: it looks clickable and does nothing.
    """
    output = _render(terminal=True)

    for line in output.splitlines():
        if "s3://" in line:
            assert "\x1b]8;;" not in line, f"the s3 row was wrapped in a link: {line!r}"


def test_citations_without_a_locator_fall_back_to_the_source_path() -> None:
    """An index built before SRC-1 has no locator; the source path is the only address."""
    buffer = io.StringIO()
    console = Console(file=buffer, width=100, force_terminal=False, no_color=True)

    _render_citations(
        console, [{"label": "Old doc", "document_id": "b" * 64, "source_path": "legacy.md"}]
    )

    assert "legacy.md" in buffer.getvalue()


# --- Streamlit ----------------------------------------------------------------


@pytest.fixture
def app_script(tmp_path: Path) -> Path:
    """A host app that renders one citation through the panel's citation renderer."""
    script = tmp_path / "app.py"
    script.write_text(
        """
import streamlit as st

from pydantic_rag_agent.ui.chat import _render_citations


class StubClient:
    def __init__(self) -> None:
        self.downloaded: list[str] = []

    def download(self, document_id: str) -> tuple[str, bytes] | None:
        self.downloaded.append(document_id)
        return "handbook.md", b"original bytes"


st.session_state.setdefault("client", StubClient())
st.session_state.setdefault("renders", 0)
st.session_state["renders"] += 1

_render_citations(
    [
        {
            "label": "Handbook > Backups, p. 4",
            "document_id": "26ec2905" + "0" * 56,
            "locator": "file:///data/documents/26/26ec2905/handbook.md",
        }
    ],
    client=st.session_state["client"],
    key="rag",
)
st.write("renders:", st.session_state["renders"])
""",
        encoding="utf-8",
    )
    return script


def test_a_citation_offers_a_download_button(app_script: Path) -> None:
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(app_script)).run()

    assert not at.exception, [str(e.value) for e in at.exception]
    assert len(at.get("download_button")) == 1


def test_the_download_is_not_fetched_while_rendering(app_script: Path) -> None:
    """The reason the button takes a callable: Streamlit re-runs the page constantly.

    If the bytes were fetched during render, every keystroke in the chat box would re-read
    the document — and over HTTP, re-request it. Proven by the stub client recording
    nothing after a run that rendered the button.
    """
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(app_script)).run()

    assert at.session_state["client"].downloaded == []
    # and it really did render, so the assertion above is not vacuous
    assert len(at.get("download_button")) == 1

"""A downloaded citation keeps the name it was ingested under.

The defect these pin: clicking "Download original" in the Streamlit panel produced a file
called ``document-26ec2905`` — no name the user recognised, and **no extension at all**, so
the operating system did not know what to open it with. Every other surface was already
correct: the API sets ``Content-Disposition`` from the catalogue row's slug, the HTTP client
parses it back, and the in-process client returns ``result.filename``. The panel was the one
place that invented a name instead of asking for the real one.
"""

from __future__ import annotations

from pydantic_rag_agent.ui.chat import _download_name

# --- the name a citation's original should be saved as ---------------------------------


def test_a_citation_original_should_keep_its_ingested_name() -> None:
    """``annual report.pdf`` downloads as ``annual report.pdf``, not as an id."""
    assert _download_name(source_path="/home/nick/notes/annual report.pdf") == "annual report.pdf"


def test_the_extension_should_survive_so_the_os_can_open_it() -> None:
    """An extensionless download is an unopenable file, however nicely it is named."""
    for source_path, expected in [
        ("/data/handbook.md", ".md"),
        ("/data/q3.xlsx", ".xlsx"),
        ("/data/slides.pptx", ".pptx"),
        ("/data/scan.pdf", ".pdf"),
        ("/data/notes.docx", ".docx"),
    ]:
        assert _download_name(source_path=source_path).endswith(expected)


def test_a_multi_part_extension_should_survive_whole() -> None:
    """``.tar.gz`` is the useful unit; truncating to ``.gz`` loses what made it readable."""
    assert _download_name(source_path="/backups/archive.tar.gz") == "archive.tar.gz"


def test_a_bare_leaf_should_be_used_as_given() -> None:
    """A URL-supplied name may already be a leaf."""
    assert _download_name(source_path="handbook.md") == "handbook.md"


def test_a_traversing_name_should_be_reduced_to_its_leaf() -> None:
    """A name is a label, never a path: it must not be able to escape the save directory."""
    assert _download_name(source_path="../../etc/passwd") == "passwd"
    assert _download_name(source_path="a/b/../c/report.pdf") == "report.pdf"


def test_a_windows_style_path_should_still_yield_its_leaf() -> None:
    """The panel is a browser; the name may have been produced on a Windows host."""
    assert _download_name(source_path=r"C:\Users\nick\Documents\report.pdf") == "report.pdf"


def test_an_empty_name_should_fall_back_rather_than_save_something_odd() -> None:
    """A citation with no usable name must still download, under a plain fallback."""
    assert _download_name(source_path="") == "document"
    assert _download_name(source_path="/") == "document"

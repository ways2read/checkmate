"""Image-report Q&A prompts (no network)."""

from pathlib import Path

from checkmate.ai.fido_image_report import ImageReport, ImageReportImage
from checkmate.ai.image_report_qa import (
    build_report_qa_system_prompt,
    restore_qa_session_from_markdown,
)
from checkmate.ai.markdown_html import append_followup_markdown
from checkmate.ai.session import ExplainSession


def test_system_prompt_mentions_inventory_and_language() -> None:
    text = build_report_qa_system_prompt("epub")
    low = text.lower()
    assert "invent" in low
    assert "epub" in low
    pdf = build_report_qa_system_prompt("pdf")
    assert "pdf" in pdf.lower()


def test_qa_brief_includes_filenames() -> None:
    report = ImageReport(
        folder=Path("."),
        document_name="book.epub",
        publication_format="epub",
        images=[
            ImageReportImage(index=1, filename="cover.png", alt_text="Cover"),
        ],
    )
    brief = report.qa_context_brief()
    assert "cover.png" in brief
    assert "Cover" in brief


def test_restore_qa_session_replays_turns() -> None:
    report = ImageReport(
        folder=Path("."),
        document_name="book.epub",
        publication_format="epub",
        images=[
            ImageReportImage(index=1, filename="cover.png", alt_text="Cover"),
        ],
    )
    md = append_followup_markdown(
        "Summary.",
        heading="Follow-up",
        question="Is the cover decorative?",
        answer="No, it needs alt.",
    )
    md = append_followup_markdown(
        md,
        heading="Follow-up",
        question="What about the chart?",
        answer="Add a longer description.",
    )
    session = ExplainSession(model="test", api_key="k")
    assert restore_qa_session_from_markdown(session, report, md) is True
    roles = [m["role"] for m in session.messages]
    assert roles[:3] == ["system", "user", "assistant"]
    assert "Is the cover decorative?" in session.messages[1]["content"]
    assert session.messages[2]["content"] == "No, it needs alt."
    assert "What about the chart?" in session.messages[3]["content"]
    assert session.messages[4]["content"] == "Add a longer description."
    assert restore_qa_session_from_markdown(session, report, md) is False

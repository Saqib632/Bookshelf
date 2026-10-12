import io

from docx import Document

from app.processing import build_chunks, clean_text, extract_document
from app.services import extension_for, normalize_filename
from app.providers import RetrievedChunk, build_answer_prompt


def test_filename_is_path_safe():
    assert normalize_filename("../../private book?.pdf") == "private_book_.pdf"
    assert extension_for("book.PDF") == "pdf"


def test_clean_and_overlap_chunks():
    pages = [{"text": "", "page_number": None}]
    assert clean_text("a\x00  b\n\n\n") == "a b"
    chunks = build_chunks([type("Page", (), {"text": "one two three four five", "page_number": 1})()], 3, 1)
    assert chunks[0]["chunk_text"] == "one two three"
    assert chunks[1]["chunk_text"].startswith("three")


def test_clean_text_preserves_headings_and_removes_page_number_lines():
    cleaned = clean_text("50\n4.5 Mockups\nThe display-\ning is clear.\n")
    assert cleaned == "4.5 Mockups\nThe displaying is clear."
    chunks = build_chunks([type("Page", (), {"text": cleaned, "page_number": 69})()], 20, 2)
    assert chunks[0]["section"] == "4.5 Mockups"


def test_docx_extraction():
    document = Document()
    document.add_paragraph("A grounded paragraph.")
    buffer = io.BytesIO()
    document.save(buffer)
    pages = extract_document(buffer.getvalue(), "docx")
    assert pages[0].text == "A grounded paragraph."


def test_answer_prompt_contains_question_and_delimited_metadata():
    evidence = [RetrievedChunk("c1", "b1", "d1", "The project assists desktop users.", 4, "Chapter 1", "Purpose", 0.9)]
    prompt = build_answer_prompt("what is the purpose?", evidence)
    assert "what is the purpose?" in prompt
    assert "<context>" in prompt and "page=4" in prompt

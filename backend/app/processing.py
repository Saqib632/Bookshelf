from __future__ import annotations

import io
import re
from dataclasses import dataclass

from docx import Document
from pypdf import PdfReader

from .services import chunk_text


@dataclass
class ExtractedPage:
    text: str
    page_number: int | None


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ").replace("\f", "\n")
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line or re.fullmatch(r"\d{1,4}", line):
            lines.append("")
        else:
            lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def extract_document(content: bytes, file_type: str) -> list[ExtractedPage]:
    if file_type == "pdf":
        reader = PdfReader(io.BytesIO(content))
        return [ExtractedPage(clean_text(page.extract_text() or ""), index + 1) for index, page in enumerate(reader.pages)]
    if file_type == "docx":
        document = Document(io.BytesIO(content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        return [ExtractedPage(clean_text(text), None)]
    raise ValueError("Unsupported document type")


def structure_for(text: str) -> tuple[str | None, str | None]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    headings = [line for line in lines if is_heading(line)]
    chapter = next((line for line in headings if line.lower().startswith("chapter ") or re.match(r"^\d+\.?\s", line)), None)
    return chapter, headings[-1] if headings else None


def is_heading(line: str) -> bool:
    return len(line) < 120 and bool(
        re.match(r"^(chapter\s+\w+|\d+(?:\.\d+)*\s+\S+)", line, re.IGNORECASE)
        or (line.isupper() and len(line.split()) <= 12)
    )


def build_chunks(pages: list[ExtractedPage], size: int, overlap: int) -> list[dict]:
    chunks: list[dict] = []
    index = 0
    for page in pages:
        if not page.text:
            continue
        chapter, section = structure_for(page.text)
        for text in chunk_text(page.text, size, overlap):
            chunks.append({"chunk_text": text, "page_number": page.page_number, "chapter": chapter, "section": section, "paragraph": None, "chunk_index": index})
            index += 1
    return chunks

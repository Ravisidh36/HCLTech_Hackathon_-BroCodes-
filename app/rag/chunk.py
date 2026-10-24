"""Step 3.4: header → table-aware → recursive chunking, ported from Argon.

Stage 1 splits on H1–H3 and records the heading path, which becomes the citation section (R2).
Stage 2 keeps Markdown tables atomic (large tables split by row groups with the header row
repeated) and splits ordinary text recursively.
"""

import re
from dataclasses import dataclass
from typing import Literal

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

HEADERS = [("#", "H1"), ("##", "H2"), ("###", "H3")]
CHUNK_SIZE = 800
CHUNK_OVERLAP = 80

_TABLE_LINE_RE = re.compile(r"^\s*\|")
_TABLE_SEP_RE = re.compile(r"^\s*\|[-| :]+\|\s*$")

ChunkType = Literal["text", "table"]


@dataclass
class ChunkDraft:
    content: str
    section_path: str
    chunk_type: ChunkType


def _extract_segments(text: str) -> list[tuple[ChunkType, str]]:
    """Partition text into alternating table and text runs (a table run = consecutive '|' lines)."""
    segments: list[tuple[ChunkType, str]] = []
    current: list[str] = []
    in_table = False
    for line in text.split("\n"):
        is_pipe = bool(_TABLE_LINE_RE.match(line))
        if is_pipe != in_table and current:
            segments.append(("table" if in_table else "text", "\n".join(current)))
            current = []
        in_table = is_pipe
        current.append(line)
    if current:
        segments.append(("table" if in_table else "text", "\n".join(current)))
    return segments


def _split_table(table: str) -> list[str]:
    """Keep a table whole if it fits; otherwise split by rows, repeating the header in every piece."""
    if len(table) <= CHUNK_SIZE:
        return [table]
    lines = table.split("\n")
    has_sep = len(lines) > 1 and bool(_TABLE_SEP_RE.match(lines[1]))
    header = lines[: 2 if has_sep else 1]
    rows = lines[2 if has_sep else 1 :]

    pieces: list[str] = []
    current: list[str] = []
    for row in rows:
        if current and len("\n".join(header + current + [row])) > CHUNK_SIZE:
            pieces.append("\n".join(header + current))
            current = []
        current.append(row)
    if current:
        pieces.append("\n".join(header + current))
    return pieces


def _section_path(metadata: dict) -> str:
    """H2 > H3 path. H1 is the document title, which the citation carries separately."""
    parts = [metadata[h] for h in ("H2", "H3") if metadata.get(h)]
    return " > ".join(parts) if parts else metadata.get("H1", "Document")


def chunk_markdown(markdown: str) -> list[ChunkDraft]:
    header_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=HEADERS)
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        # "\n" deliberately absent: table rows are newline-separated and handled by _split_table.
        separators=["\n\n", ". ", " ", ""],
    )

    drafts: list[ChunkDraft] = []
    for section in header_splitter.split_text(markdown):
        path = _section_path(section.metadata)
        for kind, content in _extract_segments(section.page_content):
            if not content.strip():
                continue
            pieces = _split_table(content) if kind == "table" else text_splitter.split_text(content)
            drafts.extend(ChunkDraft(p.strip(), path, kind) for p in pieces if p.strip())
    return drafts

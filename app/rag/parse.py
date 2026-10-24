"""Steps 3.2–3.3: turn source files into clean Markdown.

Markdown is read directly (Docling would mangle YAML front-matter). PDF, DOCX and other formats go
through Docling, which keeps tables as Markdown pipe tables.
"""

import re
from functools import lru_cache
from pathlib import Path

import yaml

MARKDOWN_SUFFIXES = {".md", ".markdown", ".txt"}
DOCLING_SUFFIXES = {".pdf", ".docx", ".pptx", ".html", ".htm"}
SUPPORTED_SUFFIXES = MARKDOWN_SUFFIXES | DOCLING_SUFFIXES

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def split_front_matter(text: str) -> tuple[dict, str]:
    """Return (front-matter dict, body). Missing front-matter yields an empty dict."""
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    return yaml.safe_load(match.group(1)) or {}, text[match.end():]


@lru_cache
def get_converter():
    from docling.document_converter import DocumentConverter

    return DocumentConverter()


def warm_docling() -> None:
    """Load Docling's models at startup so the first live upload is not slow."""
    get_converter()


def clean_markdown(md: str) -> str:
    """Argon's cleaner, minus stripping '§': hospital documents cite sections with it."""
    md = re.sub(r"\(cid:\d+\)", "", md)
    md = md.replace("ï", "")
    return md


def to_markdown(path: Path) -> str:
    """Convert a file to cleaned Markdown body text (front-matter removed)."""
    suffix = path.suffix.lower()
    if suffix in MARKDOWN_SUFFIXES:
        _, body = split_front_matter(path.read_text(encoding="utf-8"))
    elif suffix in DOCLING_SUFFIXES:
        body = get_converter().convert(str(path)).document.export_to_markdown()
    else:
        raise ValueError(f"Unsupported file type '{suffix}'. Supported: {sorted(SUPPORTED_SUFFIXES)}")

    body = clean_markdown(body)
    if not body.strip():
        raise ValueError(f"No text could be extracted from '{path.name}'.")
    return body

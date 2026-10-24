"""Chunking, cleaning, front-matter and injection screening (no models, no database)."""

from app.config import get_settings
from app.rag.chunk import CHUNK_SIZE, chunk_markdown
from app.rag.parse import clean_markdown, split_front_matter
from app.rag.screen import looks_like_injection

DOC = """# Policy Title

Intro text.

## 2. Visiting Hours

### 2.1 Standard Hours

Visiting is 10:00 to 11:00.

## 3. Coverage

| Procedure | Covered |
|---|---|
| Knee | Yes |
"""


def test_section_path_from_headings():
    paths = {c.section_path for c in chunk_markdown(DOC)}
    assert "2. Visiting Hours > 2.1 Standard Hours" in paths
    assert "Policy Title" in paths  # text before the first H2 falls back to the title


def test_table_is_its_own_chunk():
    tables = [c for c in chunk_markdown(DOC) if c.chunk_type == "table"]
    assert len(tables) == 1 and tables[0].content.startswith("| Procedure | Covered |")
    assert tables[0].section_path == "3. Coverage"


def test_large_table_repeats_header_in_every_piece():
    rows = "\n".join(f"| Procedure number {i} with a long name | Covered with conditions {i} |" for i in range(40))
    md = f"## 1. Coverage\n\n| Procedure | Covered |\n|---|---|\n{rows}\n"
    pieces = [c for c in chunk_markdown(md) if c.chunk_type == "table"]
    assert len(pieces) > 1
    assert all(p.content.startswith("| Procedure | Covered |\n|---|---|") for p in pieces)
    assert all(len(p.content) <= CHUNK_SIZE for p in pieces)


def test_cleaner_keeps_section_sign():
    assert clean_markdown("See §2.1 (cid:12)") == "See §2.1 "


def test_front_matter_split():
    meta, body = split_front_matter("---\ndoc_id: X-1\nversion: \"2.0\"\n---\n# Title\n")
    assert meta == {"doc_id": "X-1", "version": "2.0"} and body == "# Title\n"


def test_injection_screen_flags_malicious_demo_doc():
    text = (get_settings().kb_dir.parent / "uploads_demo" / "malicious-billing-faq.md").read_text(encoding="utf-8")
    assert looks_like_injection(text)


def test_injection_screen_passes_real_policies():
    for path in get_settings().kb_dir.glob("*.md"):
        assert not looks_like_injection(path.read_text(encoding="utf-8")), path.name

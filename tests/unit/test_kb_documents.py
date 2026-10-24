"""Knowledge-base files are well-formed and consistent with the seeded structured data."""

import json
import re
from datetime import date

import pytest
import yaml

from app.config import get_settings
from app.schemas.common import Authority, DocStatus

REQUIRED = {"doc_id", "title", "topic", "version", "effective_date", "authority", "status"}


def _front_matter(text: str) -> dict:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "missing front-matter"
    return yaml.safe_load(match.group(1))


KB_FILES = sorted(get_settings().kb_dir.glob("*.md"))


def test_kb_has_documents():
    assert len(KB_FILES) >= 10


@pytest.mark.parametrize("path", KB_FILES, ids=lambda p: p.name)
def test_front_matter_valid(path):
    meta = _front_matter(path.read_text(encoding="utf-8"))
    assert REQUIRED <= meta.keys()
    assert meta["authority"] in {a.value for a in Authority}
    assert meta["status"] in {s.value for s in DocStatus}
    assert isinstance(meta["version"], str), "quote the version so YAML keeps it a string"
    assert isinstance(meta["effective_date"], date)


def test_demo_cases_present():
    metas = [_front_matter(p.read_text(encoding="utf-8")) for p in KB_FILES]
    icu = {m["version"]: m["status"] for m in metas if m["doc_id"] == "POL-ICU-VISIT"}
    assert icu == {"1.0": "superseded", "2.0": "active"}

    visiting = [m for m in metas if m["topic"] == "general_ward_visiting"]
    assert {m["authority"] for m in visiting} == {"hospital_policy", "regulatory"}

    pharmacy = [m for m in metas if m["topic"] == "pharmacy_hours"]
    assert len(pharmacy) == 2
    assert len({(m["authority"], m["effective_date"]) for m in pharmacy}) == 1, "must be a true tie"


def test_preop_requirements_match_protocol():
    settings = get_settings()
    protocol = (settings.kb_dir / "preop-knee-replacement.md").read_text(encoding="utf-8")
    meta = _front_matter(protocol)
    table_codes = set(re.findall(r"^\| [^|]+ \| ([A-Z0-9]+) \|", protocol, re.MULTILINE))

    seed = json.loads((settings.seed_dir / "clinical.json").read_text(encoding="utf-8"))
    reqs = [r for r in seed["preop_requirements"] if r["procedure"] == "Knee Replacement"]
    assert {r["test_code"] for r in reqs} == table_codes
    assert all(r["doc_id"] == meta["doc_id"] and r["doc_version"] == meta["version"] for r in reqs)

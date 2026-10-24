"""Index every approved document in data/kb (metadata from each file's front-matter).

Usage: .venv\\Scripts\\python.exe -m scripts.build_index [--test]
Re-running is safe: identical files are skipped by hash.
"""

import argparse
import time

from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import make_engine
from app.rag.ingest import DocumentMeta, ingest_file
from app.rag.parse import split_front_matter


def build(url: URL) -> list:
    settings = get_settings()
    engine = make_engine(url)
    results = []
    with Session(engine) as db:
        for path in sorted(settings.kb_dir.glob("*.md")):
            meta, _ = split_front_matter(path.read_text(encoding="utf-8"))
            results.append(ingest_file(db, path, DocumentMeta(**meta), uploaded_by="seed"))
    engine.dispose()
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="index into the test database")
    args = parser.parse_args()

    settings = get_settings()
    url = settings.test_app_database_url if args.test else settings.app_database_url
    started = time.time()
    for r in build(url):
        flags = f", {r.injection_flags} injection flag(s)" if r.injection_flags else ""
        print(f"[{r.status}] {r.doc_id} v{r.version}: {r.chunks} chunks{flags}")
    print(f"Done in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()

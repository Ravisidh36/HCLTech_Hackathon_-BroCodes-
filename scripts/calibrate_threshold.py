"""Print top reranker scores for answerable vs unanswerable questions, to choose RAG_MIN_SCORE.

Usage: .venv\\Scripts\\python.exe -m scripts.calibrate_threshold
"""

from app.config import get_settings
from app.db import SessionLocal
from app.rag.retrieve import retrieve

IN_SCOPE = [
    "What are the visiting hours for ICU patients?",
    "How many visitors are allowed in the ICU?",
    "How do I schedule an MRI scan?",
    "What tests do I need before knee replacement surgery?",
    "Is HbA1c required before knee surgery?",
    "What documents should I bring for admission?",
    "When are patients discharged?",
    "What are general ward visiting hours?",
    "Is knee replacement covered under CarePlus Basic?",
    "What should I do in a medical emergency?",
    "What are the pharmacy opening hours?",
    "Do I need to fast before surgery?",
]
OUT_OF_SCOPE = [
    "What is the treatment for dragon pox?",
    "Does the hospital offer heart transplant surgery?",
    "What is the cafeteria menu today?",
    "Who won the cricket match yesterday?",
    "How do I treat a snake bite at home?",
    "What is the parking fee at the hospital?",
    "Can I bring my dog to visit?",
    "What is the chemotherapy protocol for leukaemia?",
    "How much does a dental implant cost?",
    "What are the hospital's fertility treatment options?",
]


def main() -> None:
    get_settings().rag_min_score = float("-inf")  # see raw scores for every question
    with SessionLocal() as db:
        for label, questions in (("IN ", IN_SCOPE), ("OUT", OUT_OF_SCOPE)):
            for q in questions:
                result = retrieve(db, q)
                top = result.evidence[0] if result.evidence else None
                where = f"{top.doc_id} | {top.citation.section}" if top else "-"
                print(f"{label} {result.top_score:7.2f}  {q[:55]:55} -> {where[:60]}")


if __name__ == "__main__":
    main()

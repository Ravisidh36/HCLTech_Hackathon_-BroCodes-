"""Ask the running graph a few questions end to end (real LLM, main database).

Usage: .venv\\Scripts\\python.exe -m scripts.try_chat "question one" "question two" ...
       (no arguments runs the M3 checkpoint questions)
"""

import sys

from fastapi.testclient import TestClient

from app.main import app

DEFAULT = [
    "What are the visiting hours for ICU patients?",
    "What are the visiting hours in general wards?",
    "What are the pharmacy opening hours?",
    "What is the treatment for dragon pox?",
    "Show me the lab results for P1002",
]


def main() -> None:
    questions = sys.argv[1:] or DEFAULT
    with TestClient(app) as client:
        token = client.post("/auth/login", json={"username": "patient1", "password": "Patient1@demo"}).json()
        headers = {"Authorization": f"Bearer {token['access_token']}"}
        for q in questions:
            r = client.post("/chat", json={"message": q}, headers=headers)
            body = r.json()
            print(f"\nQ: {q}\n   HTTP {r.status_code}  {body.get('answer_type')}  {body.get('latency_ms')} ms  {body.get('trace_id')}")
            print(f"   A: {body.get('answer')}")
            for c in body.get("citations", []):
                print("   cite:", c.get("document") or c.get("system"), "|", c.get("section", ""), "| v" + c.get("version", ""))


if __name__ == "__main__":
    main()

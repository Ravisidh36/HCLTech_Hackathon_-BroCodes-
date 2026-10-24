"""Step 4.6: short conversation memory for follow-ups like "reschedule it".

History is scoped to (session_id, user_id): a guessed session id never exposes another user's turns.
Stored text is PII-masked. Identity still comes only from the request context, never from history.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.platform import ChatTurn
from app.safety.pii import mask_pii

HISTORY_TURNS = 4


def save_turn(db: Session, session_id: str, user_id: int, speaker: str, content: str, answer_type: str | None = None) -> None:
    db.add(
        ChatTurn(
            session_id=session_id,
            user_id=user_id,
            speaker=speaker,
            content=mask_pii(content)[:4000],
            answer_type=answer_type,
        )
    )
    db.commit()


def recent_turns(db: Session, session_id: str, user_id: int, limit: int = HISTORY_TURNS) -> list[dict[str, str]]:
    turns = db.scalars(
        select(ChatTurn)
        .where(ChatTurn.session_id == session_id, ChatTurn.user_id == user_id)
        .order_by(ChatTurn.id.desc())
        .limit(limit)
    ).all()
    return [{"speaker": t.speaker, "content": t.content} for t in reversed(turns)]

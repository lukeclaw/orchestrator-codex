"""Chat endpoint — wired to LLM brain when API key is available."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from orchestrator.api.deps import get_db
from orchestrator.auth.keychain import get_api_key
from orchestrator.auth.token import get_validated_key, invalidate_cache
from orchestrator.state.repositories import decisions, sessions

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatMessage(BaseModel):
    message: str


@router.get("/chat/status")
def chat_status():
    """Diagnostic endpoint: check if LLM brain is available."""
    # Force fresh key lookup
    invalidate_cache()
    raw_key = get_api_key()
    validated = get_validated_key()
    return {
        "key_found": raw_key is not None,
        "key_prefix": raw_key[:12] + "..." if raw_key else None,
        "key_validated": validated is not None,
    }


@router.post("/chat")
def chat(body: ChatMessage, db=Depends(get_db)):
    """Chat with the orchestrator. Uses LLM brain if API key available, otherwise fallback."""
    # Try LLM brain first
    api_key = get_validated_key()
    logger.info("Chat request — API key available: %s", bool(api_key))

    if api_key:
        try:
            from orchestrator.llm.brain import Brain

            brain = Brain(conn=db)
            result = brain.process_query(body.message)

            actions = [
                {
                    "type": a.type,
                    "params": a.params,
                    "requires_approval": a.requires_approval,
                }
                for a in result.actions
            ]

            return {"response": result.summary, "actions": actions}
        except Exception as e:
            logger.exception("Brain failed: %s", e)
            error_msg = str(e)

            # Detect Claude Code restricted token
            if "only authorized for use with Claude Code" in error_msg:
                return {
                    "response": (
                        "The keychain token is restricted to Claude Code and cannot be used "
                        "for direct API calls.\n\n"
                        "To enable LLM chat, set ANTHROPIC_API_KEY with a Console API key:\n"
                        "  export ANTHROPIC_API_KEY=sk-ant-...\n\n"
                        "Or store one in Keychain:\n"
                        "  security add-generic-password -s claude-orchestrator "
                        "-a anthropic-api-key -w 'sk-ant-...'"
                    ),
                    "actions": [],
                    "error": "restricted_token",
                }

            return {
                "response": f"LLM error: {error_msg}",
                "actions": [],
                "error": error_msg,
            }

    # Fallback: basic status summary
    all_sessions = sessions.list_sessions(db)
    pending = decisions.list_pending(db)

    session_summary = ", ".join(
        f"{s.name} ({s.status})" for s in all_sessions
    ) or "No sessions"

    # Provide actionable guidance
    response = (
        f"Sessions: {session_summary}\n"
        f"Pending decisions: {len(pending)}\n\n"
        f"You said: {body.message}\n\n"
        f"(No API key found — ensure Claude Code is logged in, or set ANTHROPIC_API_KEY)"
    )

    return {"response": response, "actions": []}

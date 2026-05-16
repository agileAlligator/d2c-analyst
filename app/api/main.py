"""FastAPI application — chat, run viewer, health."""
import logging

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.chat.loop import run_chat
from app.config import settings
from app.warehouse.db import get_db
from app.warehouse.models import AgentRun

logger = logging.getLogger(__name__)
app = FastAPI(title="D2C Analyst", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=getattr(settings, 'allowed_origins', ["http://localhost:10002"]),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)


def get_merchant_id(x_api_key: str = Header(default=None)) -> str:
    key_map = getattr(settings, 'api_key_map', {})
    if not key_map:
        # Require explicit opt-in for keyless dev mode via DEV_MODE=true in env
        if not getattr(settings, 'dev_mode', False):
            raise HTTPException(
                status_code=500,
                detail="Server misconfiguration: API_KEYS_RAW not set. Set DEV_MODE=true to allow keyless dev access.",
            )
        logger.warning("DEV_MODE active — no API keys configured, defaulting to merchant 'demo'")
        return "demo"
    if not x_api_key or x_api_key not in key_map:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header")
    return key_map[x_api_key]


class ChatRequest(BaseModel):
    question: str
    history: list[dict] | None = None


class RoutingInfo(BaseModel):
    model: str
    tier: str
    reason: str
    escalated: bool


class ChatResponse(BaseModel):
    answer: str
    all_citations_valid: bool
    issues: list[str]
    provenance_ids: list[str]
    tool_calls: list[dict]
    routing: RoutingInfo | None = None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    db: Session = Depends(get_db),
    merchant_id: str = Depends(get_merchant_id),
):
    try:
        result = run_chat(req.question, db, merchant_id, req.history)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("Unhandled error in /chat for merchant %s", merchant_id)
        raise HTTPException(status_code=500, detail="Internal error — check server logs")
    return ChatResponse(**result)


@app.get("/runs")
def list_runs(
    db: Session = Depends(get_db),
    merchant_id: str = Depends(get_merchant_id),
):
    runs = (
        db.query(AgentRun)
        .filter(AgentRun.merchant_id == merchant_id)
        .order_by(AgentRun.started_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": str(r.id),
            "agent_name": r.agent_name,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "proposal_count": len(r.proposals or []),
        }
        for r in runs
    ]


@app.get("/runs/{run_id}")
def get_run(
    run_id: str,
    db: Session = Depends(get_db),
    merchant_id: str = Depends(get_merchant_id),
):
    run = db.query(AgentRun).filter(
        AgentRun.id == run_id,
        AgentRun.merchant_id == merchant_id,
    ).first()
    if not run:
        raise HTTPException(404, "Run not found")
    return {
        "id": str(run.id),
        "agent_name": run.agent_name,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "log_md": run.log_md,
        "proposals": run.proposals,
    }

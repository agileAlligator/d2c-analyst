"""FastAPI application — chat, run viewer, health."""
import logging

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.chat.loop import run_chat
from app.warehouse.db import get_db
from app.warehouse.models import AgentRun

logger = logging.getLogger(__name__)
app = FastAPI(title="D2C Analyst", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    question: str
    merchant_id: str = "demo"
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
def chat(req: ChatRequest, db: Session = Depends(get_db)):
    result = run_chat(req.question, db, req.merchant_id, req.history)
    return ChatResponse(**result)


@app.get("/runs")
def list_runs(merchant_id: str = "demo", db: Session = Depends(get_db)):
    runs = (
        db.query(AgentRun)
        .filter_by(merchant_id=merchant_id)
        .order_by(AgentRun.started_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "id": str(r.id),
            "agent_name": r.agent_name,
            "status": r.status,
            "started_at": str(r.started_at),
            "finished_at": str(r.finished_at),
            "proposal_count": len(r.proposals or []),
        }
        for r in runs
    ]


@app.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = db.query(AgentRun).filter(AgentRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Run not found")
    return {
        "id": str(run.id),
        "agent_name": run.agent_name,
        "status": run.status,
        "started_at": str(run.started_at),
        "finished_at": str(run.finished_at),
        "log_md": run.log_md,
        "proposals": run.proposals,
    }

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent import approve_run, continue_run, get_run_details, reject_run, start_run
from db import connection, init_db
from tools import get_campaign_metrics, inspect_events, reset_demo, set_fail_next_read


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Campaign Ops Worker", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


class RunRequest(BaseModel):
    task: str = Field(min_length=5, max_length=1500)


class AnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=1000)


@app.get("/api/health")
def health():
    with connection() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}


@app.get("/api/campaigns")
def campaigns():
    with connection() as conn:
        rows = conn.execute("SELECT campaign_id,name,source_code,channel FROM campaigns ORDER BY campaign_id").fetchall()
    result = []
    for row in rows:
        day = date(2026, 10, 4)
        metrics = get_campaign_metrics(row["campaign_id"], day.isoformat())
        duplicates = inspect_events(row["campaign_id"], day.isoformat(), "duplicates")
        result.append({**dict(row), **metrics,
                       "duplicate_deliveries": duplicates["count"],
                       "attribution_gaps": metrics["unattributed_conversions"]})
    return result


@app.post("/api/runs")
def create_run(request: RunRequest):
    try:
        return start_run(request.task.strip())
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}")
def read_run(run_id: str):
    result = get_run_details(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="Run not found")
    return result


@app.post("/api/runs/{run_id}/approve")
def approve(run_id: str):
    try:
        return approve_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/reject")
def reject(run_id: str):
    try:
        return reject_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/answer")
def answer(run_id: str, request: AnswerRequest):
    try:
        return continue_run(run_id, request.answer.strip())
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/demo/fail-next-read")
def fail_next_read():
    set_fail_next_read()
    return {"ok": True, "message": "The next campaign-list read will return one simulated temporary error."}


@app.post("/api/demo/reset")
def reset():
    reset_demo()
    return {"ok": True}


@app.get("/api/runbooks")
def runbooks():
    with connection() as conn:
        rows = conn.execute("SELECT runbook_id,title,body FROM runbooks ORDER BY runbook_id").fetchall()
    return [dict(row) for row in rows]


@app.get("/")
def index():
    frontend = Path(__file__).parent.parent / "frontend" / "dist" / "index.html"
    if frontend.exists():
        return FileResponse(frontend)
    return {"message": "Campaign Ops Worker API is running. Start the Vite frontend on port 5173."}

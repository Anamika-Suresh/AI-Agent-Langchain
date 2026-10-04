import asyncio
import csv
import io
import json
import logging
import os
import uuid
from typing import Dict, List, Optional
from contextlib import asynccontextmanager

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Query, BackgroundTasks
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

from config import PORT, HOST, DEFAULT_URL
from database import (
    init_db, get_db, DB_TYPE, SessionLocal, ExtractionJob, FounderModel, AgentLogModel
)
from agent_service import start_extraction_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("founder_agent.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Initializing Founder Extraction System database ({DB_TYPE})...")
    init_db()
    yield
    logger.info("Shutting down Founder Extraction System backend.")


app = FastAPI(
    title="AI Founder Extraction Platform",
    description="Full-stack AI Agent service for automated founder data extraction with FastAPI, WebSocket, and PostgreSQL",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure static directory exists
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, job_id: str, websocket: WebSocket):
        await websocket.accept()
        if job_id not in self.active_connections:
            self.active_connections[job_id] = []
        self.active_connections[job_id].append(websocket)
        logger.info(f"WebSocket client connected for job {job_id}. Total: {len(self.active_connections[job_id])}")

    def disconnect(self, job_id: str, websocket: WebSocket):
        if job_id in self.active_connections:
            if websocket in self.active_connections[job_id]:
                self.active_connections[job_id].remove(websocket)
            if not self.active_connections[job_id]:
                del self.active_connections[job_id]
        logger.info(f"WebSocket client disconnected for job {job_id}")

    async def broadcast_to_job(self, job_id: str, message: dict):
        if job_id in self.active_connections:
            disconnected = []
            for connection in self.active_connections[job_id]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Error sending WS message: {e}")
                    disconnected.append(connection)
            for conn in disconnected:
                self.disconnect(job_id, conn)


ws_manager = ConnectionManager()


# Request / Response Schemas
class ExtractRequest(BaseModel):
    url: str


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse(content="<h1>AI Founder Extraction API</h1><p>Frontend static file index.html loading...</p>")


@app.post("/api/extract")
async def create_extraction_job(
    req: ExtractRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL cannot be empty")
    
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url

    job_id = str(uuid.uuid4())
    job = ExtractionJob(id=job_id, url=url, status="PENDING")
    db.add(job)
    db.commit()

    async def ws_callback(data: dict):
        await ws_manager.broadcast_to_job(job_id, data)

    loop = asyncio.get_running_loop()
    # Launch agent task in background thread
    background_tasks.add_task(start_extraction_job, job_id, url, ws_callback, loop)

    return {
        "job_id": job_id,
        "url": url,
        "status": "PENDING",
        "message": "Extraction job created and started in background."
    }


@app.get("/api/jobs")
async def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(ExtractionJob).order_by(ExtractionJob.created_at.desc()).all()
    return [j.to_dict() for j in jobs]


@app.get("/api/jobs/{job_id}")
async def get_job_detail(job_id: str, db: Session = Depends(get_db)):
    job = db.query(ExtractionJob).filter(ExtractionJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Extraction job not found")
    
    result = job.to_dict()
    logs = db.query(AgentLogModel).filter(AgentLogModel.job_id == job_id).order_by(AgentLogModel.id.asc()).all()
    result["logs"] = [l.to_dict() for l in logs]
    return result


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(ExtractionJob).filter(ExtractionJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Extraction job not found")
    db.delete(job)
    db.commit()
    return {"message": f"Job {job_id} deleted successfully."}


@app.get("/api/founders")
async def list_founders(
    q: Optional[str] = Query(None, description="Search term for name, role or bio"),
    db: Session = Depends(get_db)
):
    query = db.query(FounderModel)
    if q:
        search_pattern = f"%{q.strip()}%"
        query = query.filter(
            (FounderModel.name.ilike(search_pattern)) |
            (FounderModel.role.ilike(search_pattern)) |
            (FounderModel.bio.ilike(search_pattern))
        )
    founders = query.order_by(FounderModel.created_at.desc()).all()
    return [f.to_dict() for f in founders]


@app.get("/api/stats")
async def get_stats(db: Session = Depends(get_db)):
    total_jobs = db.query(ExtractionJob).count()
    completed_jobs = db.query(ExtractionJob).filter(ExtractionJob.status == "COMPLETED").count()
    failed_jobs = db.query(ExtractionJob).filter(ExtractionJob.status == "FAILED").count()
    running_jobs = db.query(ExtractionJob).filter(ExtractionJob.status == "RUNNING").count()
    total_founders = db.query(FounderModel).count()

    success_rate = (completed_jobs / total_jobs * 100) if total_jobs > 0 else 0.0

    return {
        "total_jobs": total_jobs,
        "completed_jobs": completed_jobs,
        "failed_jobs": failed_jobs,
        "running_jobs": running_jobs,
        "total_founders": total_founders,
        "success_rate": round(success_rate, 1),
        "database_type": DB_TYPE
    }


@app.get("/api/export/{job_id}")
async def export_job_results(
    job_id: str,
    format: str = Query("json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db)
):
    job = db.query(ExtractionJob).filter(ExtractionJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Extraction job not found")

    founders = [f.to_dict() for f in job.founders]

    if format == "json":
        export_data = {
            "job_id": job.id,
            "url": job.url,
            "status": job.status,
            "extracted_at": job.completed_at.isoformat() if job.completed_at else None,
            "total_founders": len(founders),
            "founders": founders
        }
        return JSONResponse(
            content=export_data,
            headers={"Content-Disposition": f"attachment; filename=founders_{job_id[:8]}.json"}
        )
    else:  # CSV format
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Name", "Role", "LinkedIn URL", "Bio", "Previous Experience", "Evidence", "Sources"])
        for f in founders:
            writer.writerow([
                f.get("name", ""),
                f.get("role", ""),
                f.get("linkedin_url", ""),
                f.get("bio", ""),
                f.get("previous_experience", ""),
                " | ".join(f.get("evidence", [])),
                " | ".join(f.get("sources", []))
            ])
        output.seek(0)
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode("utf-8")),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=founders_{job_id[:8]}.csv"}
        )


@app.websocket("/ws/extract/{job_id}")
async def websocket_extraction_endpoint(websocket: WebSocket, job_id: str):
    await ws_manager.connect(job_id, websocket)
    try:
        # Send initial status snapshot upon connection
        db = SessionLocal()
        job = db.query(ExtractionJob).filter(ExtractionJob.id == job_id).first()
        if job:
            logs = db.query(AgentLogModel).filter(AgentLogModel.job_id == job_id).order_by(AgentLogModel.id.asc()).all()
            await websocket.send_json({
                "type": "init",
                "job": job.to_dict(),
                "logs": [l.to_dict() for l in logs]
            })
        db.close()

        # Listen for client heartbeat/messages
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        ws_manager.disconnect(job_id, websocket)
    except Exception as e:
        logger.error(f"WebSocket error on job {job_id}: {e}")
        ws_manager.disconnect(job_id, websocket)


if __name__ == "__main__":
    import uvicorn
    ports_to_try = [PORT, 8080, 8050]
    for p in dict.fromkeys(ports_to_try):
        try:
            uvicorn.run("main:app", host=HOST, port=p, reload=False)
            break
        except Exception as e:
            logger.warning(f"Port {p} unavailable ({e}). Trying next port...")

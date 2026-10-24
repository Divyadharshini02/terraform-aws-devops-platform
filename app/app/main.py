import logging
import os
import socket
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .db import Base, Task, engine, get_db

INSTANCE_ID = os.getenv("INSTANCE_ID", socket.gethostname())

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format=f"%(asctime)s level=%(levelname)s instance={INSTANCE_ID} %(message)s",
)
log = logging.getLogger("app")

REQUESTS = Counter(
    "app_http_requests_total", "HTTP requests",
    ["instance", "method", "path", "status"],
)
LATENCY = Histogram(
    "app_http_request_duration_seconds", "Request latency in seconds",
    ["instance", "method", "path"],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(engine)  # creates the table if missing
    except Exception as exc:  # DB down or another instance created it first
        log.warning("create_all skipped: %s", exc)
    yield


app = FastAPI(title="Task API", lifespan=lifespan)


@app.middleware("http")
async def observe(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Instance-ID"] = INSTANCE_ID
        return response
    finally:
        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        path = route.path if route else "unmatched"
        if path != "/metrics":
            REQUESTS.labels(INSTANCE_ID, request.method, path, str(status)).inc()
            LATENCY.labels(INSTANCE_ID, request.method, path).observe(elapsed)
            log.info("%s %s status=%s ms=%.1f", request.method, request.url.path,
                     status, elapsed * 1000)


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    done: bool = False


class TaskOut(TaskIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


@app.get("/health")
def health():
    """Liveness: the process is up. Does not touch the database."""
    return {"status": "ok", "instance": INSTANCE_ID}


@app.get("/ready")
def ready(db: Session = Depends(get_db)):
    """Readiness: the app can reach its database."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ready", "instance": INSTANCE_ID}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(body: TaskIn, db: Session = Depends(get_db)):
    task = Task(title=body.title, done=body.done)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@app.get("/tasks", response_model=list[TaskOut])
def list_tasks(db: Session = Depends(get_db)):
    return db.scalars(select(Task).order_by(Task.id)).all()


@app.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@app.put("/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, body: TaskIn, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    task.title, task.done = body.title, body.done
    db.commit()
    db.refresh(task)
    return task


@app.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    db.delete(task)
    db.commit()
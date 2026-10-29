"""FastAPI application: REST API under /api, dashboard UI at /, Swagger docs at /docs.

Start (from the project root):
    .venv\\Scripts\\python -m uvicorn app.main:app --app-dir backend --port 8000
or use scripts/start.ps1 / scripts/start.sh.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import get_settings
from .core.jobs import JobManager
from .core.service import HANFService
from .experiments.store import ExperimentStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("hanf")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    service = HANFService(settings)
    service.start()
    app.state.service = service
    app.state.jobs = JobManager()
    app.state.store = ExperimentStore(settings.results_dir, settings.graphs_dir)
    h = {a["name"]: a["status"] for a in service.adapters_health()}
    log.info("HANF ready (redis_mode=%s, adapters=%s)", settings.redis_mode, h)
    yield
    app.state.jobs.shutdown()
    service.stop()


app = FastAPI(
    title="HANF - Hybrid Adaptive NoSQL Framework",
    version="1.0.0",
    description="Middleware decision layer: polyglot routing, adaptive sharding, ML security "
                "monitoring and adaptive consistency over Redis, MongoDB and Cassandra.",
    lifespan=lifespan,
)
app.include_router(router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


_frontend = get_settings().frontend_dir
if _frontend.exists():
    app.mount("/static", StaticFiles(directory=_frontend), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(_frontend / "index.html")

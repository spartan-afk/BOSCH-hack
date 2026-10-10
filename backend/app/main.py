"""Main FastAPI application for the chaos platform."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.analyzer.report import ExperimentAnalyzer
from app.api.routes import init_dependencies, router
from app.chaos.runner import ExperimentRunner
from app.db.database import Database
from app.mapper.graph_builder import DependencyMapper
from app.monitor.k8s_client import K8sClient
from app.monitor.probe import SyntheticProbe
from app.planner.scoring import ExperimentPlanner
from app.safety.controller import SafetyController

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Global instances
db = Database()
k8s_client = None
probe = None
mapper = None
safety = None
runner = None
analyzer = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for the FastAPI app."""
    global k8s_client, probe, mapper, safety, runner, analyzer

    logger.info("Initializing chaos platform components...")
    
    try:
        k8s_client = K8sClient()
    except Exception as e:
        logger.warning(f"K8s client initialization failed (are you in a cluster or have kubeconfig?): {e}")

    probe = SyntheticProbe(frontend_url="http://localhost:30080", rps=5.0)
    mapper = DependencyMapper(k8s_client=k8s_client)
    safety = SafetyController(k8s_client=k8s_client, db=db)
    analyzer = ExperimentAnalyzer()
    runner = ExperimentRunner(
        k8s_client=k8s_client,
        probe=probe,
        safety_controller=safety,
        db=db,
        analyzer=analyzer
    )

    # Inject dependencies into routes
    init_dependencies(
        k8s=k8s_client,
        probe=probe,
        mapper=mapper,
        safety=safety,
        runner=runner,
        planner_cls=ExperimentPlanner,
        analyzer=analyzer,
        db=db
    )

    # Build initial graph and start probe
    if mapper:
        mapper.build_graph()
    if probe:
        probe.start()
        
    logger.info("Chaos platform ready.")
    
    yield
    
    logger.info("Shutting down chaos platform...")
    if probe:
        await probe.stop()


app = FastAPI(
    title="Chaos Platform",
    description="AI Chaos Engineering Platform for Online Boutique",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for the dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "*"],  # Vite default and wildcard for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

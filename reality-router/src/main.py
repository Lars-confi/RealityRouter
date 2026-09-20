"""
Main entry point for the Reality Router application
"""

import logging
import time

STARTUP_TIME = time.time()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.models.database import init_db
from src.router.auth import log_auth_status, require_api_key
from src.router.core import get_agent_card
from src.router.core import router as router_router
from src.router.metrics import router as metrics_router

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Suppress LiteLLM debug banner
import litellm

litellm.suppress_debug_info = True

# Initialize database
init_db()

# Create the FastAPI application
app = FastAPI(
    title="Reality Router",
    description="Intelligent routing system for Language Model requests",
    version="0.0.7",
)

# Inbound API-key auth; a no-op unless ROUTER_API_KEYS is set. Registered
# before CORS so CORS wraps it and 401s still carry CORS headers.
app.middleware("http")(require_api_key)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(router_router, prefix="/v1", tags=["routing"])
# app.include_router(router_router, tags=["routing_root"])
app.include_router(metrics_router, prefix="/metrics", tags=["metrics"])

# Discovery endpoints
app.get("/.well-known/agent-card.json", tags=["discovery"])(get_agent_card)


import asyncio

from src.router.core import router_core


@app.on_event("startup")
async def startup_event():
    log_auth_status()
    asyncio.create_task(router_core.run_capability_probes())


@app.get("/")
async def root():
    return {"message": "Reality Router API is running"}


@app.get("/health")
async def health_check():
    active_models = len(router_core.load_balancer.models) if hasattr(router_core, "load_balancer") else 0
    uptime = int(time.time() - STARTUP_TIME)
    return {
        "status": "healthy",
        "uptime_seconds": uptime,
        "uptime_s": uptime,
        "active_models": active_models,
        "models_active": active_models,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)

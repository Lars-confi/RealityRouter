"""
Main entry point for the LLM Router application
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.models.database import init_db
from src.router.core import router as router_router
from src.router.metrics import router as metrics_router

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize database
init_db()

# Create the FastAPI application
app = FastAPI(
    title="LLM Router",
    description="Intelligent routing system for Language Model requests",
    version="1.0.0",
)

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


@app.get("/")
async def root():
    return {"message": "LLM Router API is running"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)

"""FastAPI application entry point."""
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.schemas import (
    ErrorResponse,
    HealthResponse,
    SignupRequest,
    SignupResponse,
    TaskStatus,
    TaskStatusResponse,
)
from app.storage import storage

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    # Startup
    logger.info("Starting Epsilon Agent API...")
    await storage.connect()
    logger.info("Redis connected")
    
    yield
    
    # Shutdown
    logger.info("Shutting down Epsilon Agent API...")
    await storage.disconnect()
    logger.info("Redis disconnected")


app = FastAPI(
    title="Epsilon Agent API",
    description="Autonomous AI agent system for account creation with Playwright browser automation",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """Check the health status of the service and its dependencies."""
    redis_connected = await storage.is_connected()
    return HealthResponse(
        status="healthy" if redis_connected else "degraded",
        redis_connected=redis_connected,
        timestamp=datetime.utcnow(),
    )


@app.get(
    "/status/{task_id}",
    response_model=TaskStatusResponse,
    responses={404: {"model": ErrorResponse}},
    tags=["Tasks"],
)
async def get_task_status(task_id: str) -> TaskStatusResponse:
    """Get the current status of a signup task."""
    task = await storage.get_task(task_id)
    
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ErrorResponse(
                error="NotFound",
                message=f"Task {task_id} not found",
            ).model_dump(),
        )
    
    return task


@app.post(
    "/signup",
    response_model=SignupResponse,
    status_code=status.HTTP_201_CREATED,
    responses={500: {"model": ErrorResponse}},
    tags=["Tasks"],
)
async def create_signup_task(request: SignupRequest) -> SignupResponse:
    """
    Create a new account signup task.
    
    The task will be queued and processed asynchronously by the worker.
    Use GET /status/{task_id} to check the task status.
    """
    # Generate unique task ID
    task_id = str(uuid.uuid4())
    
    try:
        result = await storage.create_task(task_id, request)
        
        return SignupResponse(
            task_id=result.task_id,
            status=result.status,
            message=f"Signup task created successfully. Task ID: {task_id}",
            created_at=result.created_at,
        )
        
    except Exception as e:
        logger.exception(f"Failed to create signup task: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorResponse(
                error="InternalError",
                message="Failed to create signup task",
                detail={"exception": str(e)},
            ).model_dump(),
        )


@app.get("/queue/stats", tags=["Queue"])
async def get_queue_stats() -> dict:
    """Get statistics about the task queues."""
    stats = await storage.get_queue_length()
    return {
        "queues": {
            "pending": stats["pending"],
            "in_progress": stats["in_progress"],
            "completed": stats["completed"],
        },
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    """Handle HTTP exceptions with proper JSON response."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.detail,
    )


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug,
        log_level="debug" if settings.debug else "info",
    )

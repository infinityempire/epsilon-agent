"""Pydantic schemas for request validation and response models."""
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, HttpUrl


class TaskStatus(str, Enum):
    """Task execution status."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REQUIRES_MANUAL_INTERVENTION = "REQUIRES_MANUAL_INTERVENTION"


class SignupRequest(BaseModel):
    """Request model for account signup task."""

    email: EmailStr = Field(
        ...,
        description="Email address for the new account",
        examples=["user@example.com"],
    )
    username: str = Field(
        ...,
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_]+$",
        description="Desired username (alphanumeric and underscore only)",
        examples=["testuser123"],
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Account password (will be handled securely)",
        examples=["SecureP@ss123"],
    )
    target_url: Optional[HttpUrl] = Field(
        default=None,
        description="Optional custom target URL (defaults to mock signup page)",
    )
    metadata: Optional[dict[str, Any]] = Field(
        default=None,
        description="Optional metadata to attach to the task",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Optional session ID to restore (for distributed workers)",
    )
    export_session: bool = Field(
        default=False,
        description="If true, export session after successful login",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "email": "user@example.com",
                    "username": "testuser123",
                    "password": "SecureP@ss123",
                }
            ]
        }
    }


class SignupResponse(BaseModel):
    """Response model after creating a signup task."""

    task_id: str = Field(..., description="Unique task identifier")
    status: TaskStatus = Field(..., description="Current task status")
    message: str = Field(..., description="Human-readable status message")
    created_at: datetime = Field(..., description="Task creation timestamp")


class TaskStatusResponse(BaseModel):
    """Response model for task status query."""

    task_id: str = Field(..., description="Unique task identifier")
    status: TaskStatus = Field(..., description="Current task status")
    created_at: datetime = Field(..., description="Task creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last status update time")
    result: Optional[dict[str, Any]] = Field(
        None, description="Task result data (on completion)"
    )
    error_message: Optional[str] = Field(
        None, description="Error details if failed"
    )
    screenshot_path: Optional[str] = Field(
        None, description="Path to screenshot if intervention required"
    )


class HealthResponse(BaseModel):
    """Health check response model."""

    status: str = Field(..., description="Service health status")
    redis_connected: bool = Field(..., description="Redis connection status")
    timestamp: datetime = Field(..., description="Health check timestamp")


class ErrorResponse(BaseModel):
    """Standard error response model."""

    error: str = Field(..., description="Error type")
    message: str = Field(..., description="Human-readable error message")
    detail: Optional[dict[str, Any]] = Field(None, description="Additional error details")

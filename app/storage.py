"""Asynchronous Redis storage layer for task queue management."""
import json
import logging
from datetime import datetime
from typing import Any, Optional

import redis.asyncio as redis
from redis.asyncio import Redis

from app.config import settings
from app.schemas import SignupRequest, TaskStatus, TaskStatusResponse

logger = logging.getLogger(__name__)


class Storage:
    """Async Redis storage handler for task queue operations."""

    def __init__(self) -> None:
        """Initialize storage with Redis connection pool."""
        self._pool: Optional[redis.ConnectionPool] = None
        self._client: Optional[Redis] = None

    async def connect(self) -> None:
        """Establish Redis connection."""
        if self._client is None:
            self._pool = redis.ConnectionPool.from_url(
                settings.redis_url,
                max_connections=settings.redis_max_connections,
                decode_responses=True,
            )
            self._client = Redis(connection_pool=self._pool)
            await self._client.ping()
            logger.info("Redis connection established")

    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._pool:
            await self._pool.disconnect()
            self._pool = None
        logger.info("Redis connection closed")

    async def is_connected(self) -> bool:
        """Check if Redis is connected."""
        try:
            if self._client:
                await self._client.ping()
                return True
        except Exception as e:
            logger.error(f"Redis connection check failed: {e}")
        return False

    def _task_key(self, task_id: str) -> str:
        """Generate Redis key for task data."""
        return f"task:{task_id}"

    async def create_task(
        self, task_id: str, request: SignupRequest
    ) -> TaskStatusResponse:
        """Create a new task in Redis and push to queue."""
        now = datetime.utcnow()
        task_data = {
            "task_id": task_id,
            "status": TaskStatus.PENDING.value,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "request": request.model_dump(),
            "result": None,
            "error_message": None,
            "screenshot_path": None,
            "retry_count": 0,
        }

        # Store task data as hash
        await self._client.hset(
            self._task_key(task_id),
            mapping={k: json.dumps(v) if not isinstance(v, str) else v for k, v in task_data.items()},
        )

        # Set TTL for task data
        await self._client.expire(self._task_key(task_id), settings.task_ttl_seconds)

        # Push to pending queue
        await self._client.lpush(settings.task_queue_name, task_id)

        logger.info(f"Created task {task_id}")

        return TaskStatusResponse(
            task_id=task_id,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

    async def get_task(self, task_id: str) -> Optional[TaskStatusResponse]:
        """Retrieve task data from Redis."""
        key = self._task_key(task_id)
        data = await self._client.hgetall(key)

        if not data:
            return None

        return TaskStatusResponse(
            task_id=data["task_id"],
            status=TaskStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None,
            result=json.loads(data["result"]) if data.get("result") else None,
            error_message=data.get("error_message"),
            screenshot_path=data.get("screenshot_path"),
        )

    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        result: Optional[dict[str, Any]] = None,
        error_message: Optional[str] = None,
        screenshot_path: Optional[str] = None,
    ) -> None:
        """Update task status in Redis."""
        now = datetime.utcnow()
        updates: dict[str, str] = {
            "status": status.value,
            "updated_at": now.isoformat(),
        }

        if result is not None:
            updates["result"] = json.dumps(result)
        if error_message is not None:
            updates["error_message"] = error_message
        if screenshot_path is not None:
            updates["screenshot_path"] = screenshot_path

        await self._client.hset(self._task_key(task_id), mapping=updates)
        await self._client.expire(self._task_key(task_id), settings.task_ttl_seconds)

        logger.info(f"Updated task {task_id} to status {status.value}")

    async def pop_task_from_queue(self) -> Optional[str]:
        """Pop a task from the pending queue (FIFO)."""
        # Use BRPOP for blocking pop with timeout
        result = await self._client.brpop(settings.task_queue_name, timeout=1)
        if result:
            _, task_id = result
            return task_id
        return None

    async def increment_retry_count(self, task_id: str) -> int:
        """Increment retry count and return new value."""
        count = await self._client.hincrby(self._task_key(task_id), "retry_count", 1)
        return int(count)

    async def get_retry_count(self, task_id: str) -> int:
        """Get current retry count for a task."""
        count = await self._client.hget(self._task_key(task_id), "retry_count")
        return int(count) if count else 0

    async def move_to_completed(self, task_id: str) -> None:
        """Move task to completed queue."""
        await self._client.lpush(settings.completed_queue_name, task_id)

    async def get_queue_length(self) -> dict[str, int]:
        """Get lengths of all task queues."""
        return {
            "pending": await self._client.llen(settings.task_queue_name),
            "in_progress": await self._client.llen(settings.in_progress_queue_name),
            "completed": await self._client.llen(settings.completed_queue_name),
        }


# Global storage instance
storage = Storage()

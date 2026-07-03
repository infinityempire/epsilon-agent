"""Asynchronous Redis storage layer for task queue management."""
import json
import logging
import time
from datetime import datetime
from typing import Any, Optional

import redis.asyncio as redis
from redis.asyncio import Redis

from app.config import settings
from app.schemas import SignupRequest, TaskStatus, TaskStatusResponse

logger = logging.getLogger(__name__)

# Metrics keys
METRICS_KEY = "epsilon:metrics"
PROCESSING_TIMES_KEY = "epsilon:processing_times"


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

    # -----------------------------------------------------------------------
    # Metrics & Observability
    # -----------------------------------------------------------------------
    async def record_task_start(self, task_id: str) -> None:
        """Record task processing start time for latency tracking."""
        await self._client.hset(
            f"{METRICS_KEY}:processing",
            task_id,
            str(time.time())
        )

    async def record_task_completion(self, task_id: str, status: TaskStatus) -> None:
        """Record task completion and update metrics."""
        # Calculate processing time
        start_time_str = await self._client.hget(f"{METRICS_KEY}:processing", task_id)
        if start_time_str:
            try:
                start_time = float(start_time_str)
                processing_time = time.time() - start_time
                # Store processing time for analytics
                await self._client.lpush(PROCESSING_TIMES_KEY, processing_time)
                # Keep only last 1000 processing times
                await self._client.ltrim(PROCESSING_TIMES_KEY, 0, 999)
            except Exception:
                pass
            # Clean up processing record
            await self._client.hdel(f"{METRICS_KEY}:processing", task_id)

        # Update status counters
        status_key = f"{METRICS_KEY}:status:{status.value}"
        await self._client.incr(status_key)
        await self._client.expire(status_key, settings.task_ttl_seconds)

    async def record_task_retry(self, task_id: str) -> None:
        """Record a task retry for retry rate tracking."""
        await self._client.hincrby(f"{METRICS_KEY}:retries", task_id, 1)
        await self._client.expire(f"{METRICS_KEY}:retries", settings.task_ttl_seconds)

    async def get_queue_stats(self) -> dict[str, Any]:
        """Get comprehensive queue statistics including metrics."""
        # Basic queue lengths
        queues = await self.get_queue_length()
        
        # Status counters
        status_counts = {}
        for status in TaskStatus:
            count = await self._client.get(f"{METRICS_KEY}:status:{status.value}")
            status_counts[status.value] = int(count) if count else 0
        
        # Retry statistics
        retry_total = 0
        retry_data = await self._client.hgetall(f"{METRICS_KEY}:retries")
        if retry_data:
            retry_total = sum(int(v) for v in retry_data.values())
        
        # Processing time statistics
        processing_times = []
        raw_times = await self._client.lrange(PROCESSING_TIMES_KEY, 0, 99)
        for t in raw_times:
            try:
                processing_times.append(float(t))
            except Exception:
                pass
        
        avg_processing_time = sum(processing_times) / len(processing_times) if processing_times else 0
        min_processing_time = min(processing_times) if processing_times else 0
        max_processing_time = max(processing_times) if processing_times else 0
        
        # Count active processing tasks
        processing_count = await self._client.hlen(f"{METRICS_KEY}:processing")
        
        return {
            "queues": queues,
            "status_counts": status_counts,
            "retry_stats": {
                "total_retries": retry_total,
                "tasks_with_retries": len(retry_data) if retry_data else 0,
            },
            "processing_stats": {
                "active_tasks": processing_count,
                "avg_processing_time_ms": round(avg_processing_time * 1000, 2),
                "min_processing_time_ms": round(min_processing_time * 1000, 2),
                "max_processing_time_ms": round(max_processing_time * 1000, 2),
                "sample_size": len(processing_times),
            },
            "total_tasks": sum(status_counts.values()),
            "success_rate": round(
                status_counts.get("COMPLETED", 0) / max(sum(status_counts.values()), 1) * 100, 2
            ),
        }

    async def get_task_age(self, task_id: str) -> Optional[float]:
        """Get the age of a task in seconds since creation."""
        task = await self.get_task(task_id)
        if task and task.created_at:
            return (datetime.utcnow() - task.created_at).total_seconds()
        return None

    async def cleanup_old_tasks(self, max_age_seconds: int = 86400 * 7) -> int:
        """Remove task data older than max_age_seconds. Returns count of removed tasks."""
        # This is a simplified cleanup - in production you'd want to scan all task keys
        removed = 0
        pattern = "task:*"
        cursor = 0
        while True:
            cursor, keys = await self._client.scan(cursor, match=pattern, count=100)
            for key in keys:
                ttl = await self._client.ttl(key)
                if ttl == -1:  # No TTL set
                    created_at_str = await self._client.hget(key, "created_at")
                    if created_at_str:
                        try:
                            created_at = datetime.fromisoformat(created_at_str)
                            age = (datetime.utcnow() - created_at).total_seconds()
                            if age > max_age_seconds:
                                await self._client.delete(key)
                                removed += 1
                        except Exception:
                            pass
            if cursor == 0:
                break
        return removed


# Global storage instance
storage = Storage()


class SessionManager:
    """Manages browser session persistence for distributed workers."""

    SESSION_PREFIX = "epsilon:session:"

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    async def export_session(self, platform: str, context, metadata: Optional[dict] = None) -> str:
        """
        Export a browser session (cookies + storage state) to Redis.
        
        Args:
            platform: Platform identifier (e.g., 'bluesky', 'mastodon')
            context: Playwright BrowserContext to export from
            metadata: Optional metadata about the session
            
        Returns:
            Session ID that can be used to import the session later
        """
        import uuid
        session_id = str(uuid.uuid4())
        
        # Get cookies from context
        cookies = await context.cookies()
        
        # Get localStorage/sessionStorage via JavaScript
        storage_state = await context.storage_state()
        
        session_data = {
            "session_id": session_id,
            "platform": platform,
            "cookies": cookies,
            "origins": storage_state.get("cookies", []),
            "local_storage": storage_state.get("localStorage", []),
            "session_storage": storage_state.get("sessionStorage", []),
            "metadata": metadata or {},
            "created_at": datetime.utcnow().isoformat(),
        }
        
        # Store in Redis with TTL
        key = f"{self.SESSION_PREFIX}{platform}:{session_id}"
        await self._storage._client.set(
            key,
            json.dumps(session_data),
            ex=settings.task_ttl_seconds * 7  # Keep sessions longer than tasks
        )
        
        logger.info(f"Exported session {session_id} for platform {platform}")
        return session_id

    async def import_session(self, platform: str, session_id: str, context) -> bool:
        """
        Import a browser session from Redis into a Playwright context.
        
        Args:
            platform: Platform identifier
            session_id: Session ID returned from export_session
            context: Playwright BrowserContext to import into
            
        Returns:
            True if session was imported successfully
        """
        key = f"{self.SESSION_PREFIX}{platform}:{session_id}"
        session_data_str = await self._storage._client.get(key)
        
        if not session_data_str:
            logger.warning(f"Session {session_id} not found for platform {platform}")
            return False
        
        try:
            session_data = json.loads(session_data_str)
            
            # Add cookies to context
            cookies = session_data.get("cookies", [])
            if cookies:
                await context.add_cookies(cookies)
            
            logger.info(f"Imported session {session_id} for platform {platform}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to import session {session_id}: {e}")
            return False

    async def get_session(self, platform: str, session_id: str) -> Optional[dict]:
        """Get session metadata without importing."""
        key = f"{self.SESSION_PREFIX}{platform}:{session_id}"
        data = await self._storage._client.get(key)
        if data:
            return json.loads(data)
        return None

    async def list_sessions(self, platform: Optional[str] = None) -> list[dict]:
        """List all sessions, optionally filtered by platform."""
        pattern = f"{self.SESSION_PREFIX}{platform or '*'}:*"
        sessions = []
        cursor = 0
        while True:
            cursor, keys = await self._storage._client.scan(cursor, match=pattern, count=100)
            for key in keys:
                data = await self._storage._client.get(key)
                if data:
                    sessions.append(json.loads(data))
            if cursor == 0:
                break
        return sessions

    async def delete_session(self, platform: str, session_id: str) -> bool:
        """Delete a session from storage."""
        key = f"{self.SESSION_PREFIX}{platform}:{session_id}"
        result = await self._storage._client.delete(key)
        return result > 0


# Global session manager instance
session_manager = SessionManager(storage)

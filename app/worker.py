"""Async worker for processing signup tasks from Redis queue."""
import asyncio
import logging
import signal
import sys
from typing import Optional

from app.browser_agent import BrowserAgent
from app.config import settings
from app.schemas import SignupRequest, TaskStatus
from app.storage import storage

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class Worker:
    """Async worker that polls Redis queue and processes signup tasks."""

    def __init__(self) -> None:
        """Initialize the worker."""
        self._running: bool = False
        self._browser_agent: Optional[BrowserAgent] = None
        self._shutdown_event: asyncio.Event = asyncio.Event()

    async def initialize(self) -> None:
        """Initialize worker components."""
        logger.info("Initializing worker...")
        
        # Connect to Redis
        await storage.connect()
        logger.info("Redis connection established")
        
        # Initialize browser agent
        self._browser_agent = BrowserAgent()
        await self._browser_agent.initialize()
        logger.info("Browser agent initialized")
        
        self._running = True
        logger.info("Worker initialization complete")

    async def shutdown(self) -> None:
        """Gracefully shutdown the worker."""
        logger.info("Initiating worker shutdown...")
        self._running = False
        self._shutdown_event.set()
        
        # Cleanup browser agent
        if self._browser_agent:
            await self._browser_agent.cleanup()
            logger.info("Browser agent cleaned up")
        
        # Disconnect from Redis
        await storage.disconnect()
        logger.info("Redis disconnected")
        
        logger.info("Worker shutdown complete")

    async def process_task(self, task_id: str) -> bool:
        """
        Process a single signup task.
        
        Args:
            task_id: The ID of the task to process
            
        Returns:
            True if task was processed successfully, False otherwise
        """
        logger.info(f"Processing task: {task_id}")
        
        try:
            # Get task data
            task = await storage.get_task(task_id)
            if task is None:
                logger.error(f"Task {task_id} not found")
                return False
            
            # Update status to IN_PROGRESS
            await storage.update_task_status(task_id, TaskStatus.IN_PROGRESS)
            
            # Parse request data
            request_data = task.result or {}
            if "request" in request_data:
                request_data = request_data["request"]
            
            signup_request = SignupRequest(**request_data)
            
            # Execute the signup automation
            if self._browser_agent is None:
                raise RuntimeError("Browser agent not initialized")
            
            result = await self._browser_agent.execute_signup(task_id, signup_request)
            
            # Update task with result
            await storage.update_task_status(
                task_id,
                status=result.status,
                result=result.result,
                error_message=result.error_message,
                screenshot_path=result.screenshot_path,
            )
            
            # Move to appropriate queue
            if result.status == TaskStatus.COMPLETED:
                await storage.move_to_completed(task_id)
                logger.info(f"Task {task_id} completed successfully")
            elif result.status == TaskStatus.REQUIRES_MANUAL_INTERVENTION:
                logger.warning(f"Task {task_id} requires manual intervention")
            else:
                logger.error(f"Task {task_id} failed: {result.error_message}")
            
            return True
            
        except Exception as e:
            logger.exception(f"Error processing task {task_id}: {e}")
            
            # Check retry count
            retry_count = await storage.increment_retry_count(task_id)
            
            if retry_count >= settings.max_retries:
                await storage.update_task_status(
                    task_id,
                    status=TaskStatus.FAILED,
                    error_message=f"Max retries exceeded: {str(e)}",
                )
                logger.error(f"Task {task_id} failed after {retry_count} retries")
            else:
                # Put back in queue for retry
                await storage.update_task_status(
                    task_id,
                    status=TaskStatus.PENDING,
                    error_message=f"Retry {retry_count}/{settings.max_retries}: {str(e)}",
                )
                # Push back to queue
                from app.config import settings as cfg
                await storage._client.lpush(cfg.task_queue_name, task_id)
                logger.info(f"Task {task_id} requeued for retry {retry_count}/{settings.max_retries}")
            
            return False

    async def run(self) -> None:
        """Run the worker loop."""
        logger.info("Starting worker loop...")
        
        while self._running:
            try:
                # Poll for new task
                task_id = await storage.pop_task_from_queue()
                
                if task_id is None:
                    # No task available, wait before polling again
                    try:
                        await asyncio.wait_for(
                            self._shutdown_event.wait(),
                            timeout=settings.worker_poll_interval_seconds,
                        )
                    except asyncio.TimeoutError:
                        continue  # Timeout is expected, continue polling
                else:
                    # Process the task
                    await self.process_task(task_id)
                    
            except asyncio.CancelledError:
                logger.info("Worker loop cancelled")
                break
            except Exception as e:
                logger.exception(f"Error in worker loop: {e}")
                # Brief pause before continuing
                await asyncio.sleep(1)
        
        logger.info("Worker loop ended")


async def main() -> None:
    """Main entry point for the worker."""
    worker = Worker()
    
    # Setup signal handlers
    loop = asyncio.get_running_loop()
    
    def signal_handler() -> None:
        logger.info("Received shutdown signal")
        asyncio.create_task(worker.shutdown())
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
    
    try:
        await worker.initialize()
        await worker.run()
    except Exception as e:
        logger.exception(f"Worker error: {e}")
    finally:
        await worker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

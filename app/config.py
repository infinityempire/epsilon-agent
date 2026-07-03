"""Application configuration using Pydantic BaseSettings."""
from pathlib import Path
from typing import Final

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Redis Configuration
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 20
    task_ttl_seconds: int = 86400  # 24 hours

    # Browser/Playwright Configuration
    headless: bool = True
    browser_channel: str = "chromium"
    browser_timeout_ms: int = 30000
    navigation_timeout_ms: int = 60000
    screenshot_dir: Path = Path(__file__).parent / "screenshots"

    # Worker Configuration
    worker_poll_interval_seconds: float = 1.0
    worker_batch_size: int = 5
    max_retries: int = 3

    # Task Queue Names
    task_queue_name: Final[str] = "signup_tasks:pending"
    in_progress_queue_name: Final[str] = "signup_tasks:in_progress"
    completed_queue_name: Final[str] = "signup_tasks:completed"

    # Mock Target Website (for demo)
    mock_target_url: str = "https://example.com/signup"

    # Application
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = False

    def ensure_screenshot_dir(self) -> Path:
        """Ensure screenshot directory exists."""
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        return self.screenshot_dir


settings = Settings()

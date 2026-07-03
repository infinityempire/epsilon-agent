"""Tests for the Epsilon Agent API."""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas import (
    SignupRequest,
    SignupResponse,
    TaskStatus,
    TaskStatusResponse,
)
from app.config import Settings


class TestSchemas:
    """Test Pydantic schemas."""

    def test_signup_request_valid(self):
        """Test valid signup request."""
        request = SignupRequest(
            email="user@example.com",
            username="testuser123",
            password="SecureP@ss123",
        )
        assert request.email == "user@example.com"
        assert request.username == "testuser123"
        assert request.password == "SecureP@ss123"

    def test_signup_request_invalid_email(self):
        """Test signup request with invalid email."""
        with pytest.raises(ValueError):
            SignupRequest(
                email="invalid-email",
                username="testuser",
                password="SecureP@ss123",
            )

    def test_signup_request_short_username(self):
        """Test signup request with username too short."""
        with pytest.raises(ValueError):
            SignupRequest(
                email="user@example.com",
                username="ab",  # Less than 3 chars
                password="SecureP@ss123",
            )

    def test_signup_request_short_password(self):
        """Test signup request with password too short."""
        with pytest.raises(ValueError):
            SignupRequest(
                email="user@example.com",
                username="testuser",
                password="short",  # Less than 8 chars
            )

    def test_task_status_response(self):
        """Test task status response."""
        response = TaskStatusResponse(
            task_id="test-123",
            status=TaskStatus.PENDING,
            created_at=datetime.utcnow(),
        )
        assert response.task_id == "test-123"
        assert response.status == TaskStatus.PENDING
        assert response.result is None


class TestConfig:
    """Test configuration."""

    def test_settings_defaults(self):
        """Test default settings values."""
        settings = Settings()
        assert settings.redis_url == "redis://localhost:6379/0"
        assert settings.headless is True
        assert settings.max_retries == 3
        assert settings.task_ttl_seconds == 86400

    def test_settings_custom_values(self):
        """Test custom settings values."""
        settings = Settings(
            redis_url="redis://custom:6379/1",
            headless=False,
            max_retries=5,
        )
        assert settings.redis_url == "redis://custom:6379/1"
        assert settings.headless is False
        assert settings.max_retries == 5


class TestBrowserAgent:
    """Test browser agent (mocked)."""

    @pytest.mark.asyncio
    async def test_human_delay(self):
        """Test human-like delay."""
        from app.browser_agent import BrowserAgent
        
        agent = BrowserAgent()
        # Just ensure it doesn't raise
        await agent._human_delay(100, 200)

    def test_realistic_user_agent(self):
        """Test user agent generation."""
        from app.browser_agent import BrowserAgent
        
        agent = BrowserAgent()
        ua = agent._get_realistic_user_agent()
        assert "Chrome" in ua
        assert "Windows" in ua


class TestInboxService:
    """Test inbox service (mocked)."""

    @pytest.mark.asyncio
    async def test_get_verification_code(self):
        """Test getting verification code."""
        from app.browser_agent import InboxService
        
        service = InboxService()
        code = await service.get_verification_code("test@example.com", timeout_seconds=5)
        
        # Simulated code should be 6 digits
        assert code is not None
        assert len(code) == 6
        assert code.isdigit()

    @pytest.mark.asyncio
    async def test_setup_inbox(self):
        """Test inbox setup."""
        from app.browser_agent import InboxService
        
        service = InboxService()
        result = await service.setup_inbox("test@example.com")
        assert result is True

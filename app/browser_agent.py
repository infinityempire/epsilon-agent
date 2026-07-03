"""Async Playwright browser automation for account creation."""
import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    async_playwright,
)

from app.config import settings
from app.schemas import SignupRequest, TaskStatus

logger = logging.getLogger(__name__)

# CAPTCHA/bot detection patterns to detect
CAPTCHA_SELECTORS = [
    "iframe[src*='captcha']",
    "[class*='captcha']",
    "[id*='captcha']",
    ".g-recaptcha",
    "[data-sitekey]",
    "[class*='recaptcha']",
    "#captcha",
    ".hcaptcha",
    "[class*='hcaptcha']",
]

# Bot/anti-bot overlay patterns
BOT_DETECTION_SELECTORS = [
    "[class*='bot-detect']",
    "[id*='bot-detect']",
    "[class*='antibot']",
    "[id*='antibot']",
    "[class*='challenge']",
    "[id*='challenge']",
    "[class*='blocked']",
    "[class*='verification']",
    "iframe[src*='challenge']",
]


@dataclass
class AutomationResult:
    """Result of an automation execution."""

    status: TaskStatus
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    screenshot_path: Optional[str] = None


class InboxService:
    """
    Abstract inbox service for receiving confirmation codes.
    This is a stub implementation that simulates OTP/code receipt.
    """

    async def get_verification_code(self, email: str, timeout_seconds: int = 60) -> Optional[str]:
        """
        Wait for and retrieve a verification code from the inbox.
        
        In a real implementation, this would:
        - Connect to an email provider API (Gmail, SendGrid, etc.)
        - Poll for new emails to the target address
        - Extract the verification code from the email body
        
        For this MVP, we simulate receiving a code after a delay.
        """
        logger.info(f"Waiting for verification code for {email}")
        
        # Simulate network delay for email delivery
        await asyncio.sleep(random.uniform(2, 5))
        
        # Generate a simulated 6-digit code
        simulated_code = str(random.randint(100000, 999999))
        logger.info(f"Simulated verification code received: {simulated_code}")
        
        return simulated_code

    async def setup_inbox(self, email: str) -> bool:
        """
        Set up an inbox for the given email address.
        Returns True if setup was successful.
        """
        logger.info(f"Setting up inbox for {email}")
        # In real implementation, this would create/register the email address
        return True

    async def cleanup_inbox(self, email: str) -> None:
        """Clean up the inbox after use."""
        logger.info(f"Cleaning up inbox for {email}")


class BrowserAgent:
    """
    Async browser automation agent for account creation.
    Uses Playwright to control a headless browser.
    """

    def __init__(self) -> None:
        """Initialize the browser agent."""
        self._playwright: Optional[Playwright] = None
        self._browser_type: Optional[BrowserType] = None
        self._inbox_service = InboxService()
        self._context: Optional[BrowserContext] = None

    async def initialize(self) -> None:
        """Initialize Playwright and browser."""
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            self._browser_type = self._playwright.chromium

    async def cleanup(self) -> None:
        """Clean up Playwright resources."""
        if self._context:
            await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def _ensure_context(self) -> BrowserContext:
        """Ensure a fresh browser context exists."""
        if self._context is None:
            browser = await self._browser_type.launch(
                headless=settings.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            # Each task gets an isolated context
            self._context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent=self._get_realistic_user_agent(),
                locale="en-US",
            )
            # Set realistic browser properties
            await self._context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
            """)
        return self._context

    def _get_realistic_user_agent(self) -> str:
        """Generate a realistic user agent string."""
        chrome_versions = ["120.0.0.0", "121.0.0.0", "122.0.0.0"]
        base = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random.choice(chrome_versions)} Safari/537.36"
        return base

    async def _check_for_blocking_elements(self, page: Page) -> tuple[bool, str]:
        """Check if page contains CAPTCHA or bot detection elements."""
        for selector in CAPTCHA_SELECTORS + BOT_DETECTION_SELECTORS:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    logger.warning(f"Blocking element detected: {selector}")
                    return True, selector
            except Exception:
                continue
        return False, ""

    async def _take_screenshot(self, page: Page, task_id: str, prefix: str = "error") -> str:
        """Take a screenshot and save to disk."""
        settings.ensure_screenshot_dir()
        timestamp = int(time.time())
        filename = f"{prefix}_{task_id}_{timestamp}.png"
        filepath = settings.screenshot_dir / filename
        await page.screenshot(path=str(filepath), full_page=True)
        logger.info(f"Screenshot saved to {filepath}")
        return str(filepath)

    async def _human_delay(self, min_ms: int = 500, max_ms: int = 2000) -> None:
        """Simulate human-like delays between actions."""
        delay = random.uniform(min_ms, max_ms) / 1000
        await asyncio.sleep(delay)

    async def execute_signup(self, task_id: str, request: SignupRequest) -> AutomationResult:
        """
        Execute the account signup workflow.
        
        Args:
            task_id: Unique identifier for this task
            request: Signup request data
            
        Returns:
            AutomationResult with status and any result/error data
        """
        page: Optional[Page] = None
        
        try:
            # Initialize if needed
            await self.initialize()
            
            # Set up inbox
            await self._inbox_service.setup_inbox(request.email)
            
            # Create isolated browser context
            context = await self._ensure_context()
            page = await context.new_page()
            
            # Navigate to signup page
            target_url = str(request.target_url) if request.target_url else settings.mock_target_url
            logger.info(f"Navigating to {target_url}")
            
            try:
                await page.goto(
                    target_url,
                    timeout=settings.navigation_timeout_ms,
                    wait_until="domcontentloaded",
                )
            except PlaywrightTimeout:
                return AutomationResult(
                    status=TaskStatus.FAILED,
                    error_message=f"Navigation timeout to {target_url}",
                )
            
            await self._human_delay(1000, 3000)
            
            # Check for blocking elements after navigation
            blocked, selector = await self._check_for_blocking_elements(page)
            if blocked:
                screenshot_path = await self._take_screenshot(page, task_id, "blocked")
                await self._inbox_service.cleanup_inbox(request.email)
                return AutomationResult(
                    status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                    error_message=f"CAPTCHA or blocking element detected: {selector}",
                    screenshot_path=screenshot_path,
                )
            
            # Fill signup form fields
            signup_result = await self._fill_signup_form(page, request)
            if signup_result is not None:
                return signup_result
            
            # Submit form
            await self._human_delay(500, 1500)
            
            try:
                submit_button = await page.query_selector(
                    "button[type='submit'], input[type='submit'], button:has-text('Sign up'), button:has-text('Create'), button:has-text('Register')"
                )
                if submit_button:
                    await submit_button.click()
                    await self._human_delay(2000, 5000)
                else:
                    logger.warning("No submit button found, form may auto-submit")
            except Exception as e:
                logger.error(f"Error clicking submit: {e}")
            
            # Check for blocking elements after submission
            blocked, selector = await self._check_for_blocking_elements(page)
            if blocked:
                screenshot_path = await self._take_screenshot(page, task_id, "blocked_post_submit")
                await self._inbox_service.cleanup_inbox(request.email)
                return AutomationResult(
                    status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                    error_message=f"CAPTCHA/bot detection appeared after submission: {selector}",
                    screenshot_path=screenshot_path,
                )
            
            # Wait for email verification step
            verification_result = await self._handle_verification(page, request)
            if verification_result is not None:
                await self._inbox_service.cleanup_inbox(request.email)
                return verification_result
            
            # Success - account created
            await self._inbox_service.cleanup_inbox(request.email)
            return AutomationResult(
                status=TaskStatus.COMPLETED,
                result={
                    "email": request.email,
                    "username": request.username,
                    "message": "Account created successfully",
                },
            )
            
        except PlaywrightTimeout as e:
            screenshot_path = await self._take_screenshot(page, task_id, "timeout") if page else None
            await self._inbox_service.cleanup_inbox(request.email)
            return AutomationResult(
                status=TaskStatus.FAILED,
                error_message=f"Browser operation timed out: {str(e)}",
                screenshot_path=screenshot_path,
            )
            
        except Exception as e:
            screenshot_path = await self._take_screenshot(page, task_id, "exception") if page else None
            await self._inbox_service.cleanup_inbox(request.email)
            logger.exception(f"Unexpected error during signup: {e}")
            return AutomationResult(
                status=TaskStatus.FAILED,
                error_message=f"Unexpected error: {str(e)}",
                screenshot_path=screenshot_path,
            )
            
        finally:
            if page:
                await page.close()

    async def _fill_signup_form(self, page: Page, request: SignupRequest) -> Optional[AutomationResult]:
        """
        Fill in the signup form fields.
        Returns None on success, or an AutomationResult if we need to abort.
        """
        task_id = "unknown"  # Will be set by caller
        
        # Common field selectors for signup forms
        field_mappings = {
            "email": [
                "input[name='email']",
                "input[name='emailAddress']",
                "input[id='email']",
                "input[type='email']",
                "input[placeholder*='email']",
                "input[placeholder*='Email']",
            ],
            "username": [
                "input[name='username']",
                "input[name='user']",
                "input[id='username']",
                "input[placeholder*='username']",
                "input[placeholder*='Username']",
                "input[placeholder*='user name']",
            ],
            "password": [
                "input[name='password']",
                "input[name='pwd']",
                "input[id='password']",
                "input[type='password']",
                "input[placeholder*='password']",
                "input[placeholder*='Password']",
            ],
            "confirm_password": [
                "input[name='confirmPassword']",
                "input[name='confirm_password']",
                "input[name='passwordConfirm']",
                "input[id='confirmPassword']",
                "input[placeholder*='confirm']",
            ],
        }
        
        async def fill_field(field_type: str, value: str) -> bool:
            """Try to fill a field using various selectors."""
            selectors = field_mappings.get(field_type, [])
            for selector in selectors:
                try:
                    element = await page.query_selector(selector)
                    if element and await element.is_visible():
                        await element.fill("")
                        await element.type(value, delay=random.uniform(50, 150))
                        logger.info(f"Filled {field_type} field")
                        return True
                except Exception:
                    continue
            return False
        
        # Fill email
        if not await fill_field("email", request.email):
            logger.warning("Could not find email field")
            
        await self._human_delay(300, 800)
        
        # Fill username
        if not await fill_field("username", request.username):
            logger.warning("Could not find username field")
            
        await self._human_delay(300, 800)
        
        # Fill password
        if not await fill_field("password", request.password):
            logger.warning("Could not find password field")
            
        await self._human_delay(300, 800)
        
        # Fill confirm password if field exists
        confirm_selectors = field_mappings["confirm_password"]
        for selector in confirm_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    await element.fill("")
                    await element.type(request.password, delay=random.uniform(50, 150))
                    logger.info("Filled confirm password field")
                    break
            except Exception:
                continue
        
        return None

    async def _handle_verification(
        self, page: Page, request: SignupRequest
    ) -> Optional[AutomationResult]:
        """
        Handle email verification step if required.
        Returns None to continue, or an AutomationResult to abort.
        """
        # Look for verification code input
        verification_selectors = [
            "input[name='code']",
            "input[name='verificationCode']",
            "input[name='otp']",
            "input[id='verification-code']",
            "input[placeholder*='code']",
            "input[placeholder*='Code']",
            "input[placeholder*='verification']",
            "input[maxlength='6']",
            "input[maxlength='4']",
        ]
        
        task_id = "unknown"
        
        for selector in verification_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    logger.info(f"Verification field found: {selector}")
                    
                    # Check for blocking elements first
                    blocked, block_selector = await self._check_for_blocking_elements(page)
                    if blocked:
                        screenshot_path = await self._take_screenshot(page, task_id, "blocked_verification")
                        return AutomationResult(
                            status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                            error_message=f"CAPTCHA appeared during verification: {block_selector}",
                            screenshot_path=screenshot_path,
                        )
                    
                    # Get verification code from inbox
                    code = await self._inbox_service.get_verification_code(
                        request.email, timeout_seconds=120
                    )
                    
                    if code:
                        await element.fill("")
                        await element.type(code, delay=random.uniform(50, 150))
                        
                        # Look for verify button
                        verify_button = await page.query_selector(
                            "button:has-text('Verify'), button:has-text('Confirm'), button:has-text('Submit')"
                        )
                        if verify_button:
                            await verify_button.click()
                            
                        await self._human_delay(2000, 4000)
                        
                        # Check for success
                        if await self._check_success(page):
                            return None  # Success, continue normally
                    else:
                        screenshot_path = await self._take_screenshot(page, task_id, "no_verification_code")
                        return AutomationResult(
                            status=TaskStatus.FAILED,
                            error_message="Could not retrieve verification code from inbox",
                            screenshot_path=screenshot_path,
                        )
                    break
            except Exception:
                continue
        
        return None

    async def _check_success(self, page: Page) -> bool:
        """Check if the page indicates successful account creation."""
        success_indicators = [
            "[class*='success']",
            "[class*='confirmed']",
            "[class*='welcome']",
            "text='Account created'",
            "text='Welcome'",
            "text='Verified'",
            "text='Confirmation'",
            "text='Success'",
        ]
        
        for indicator in success_indicators:
            try:
                if "text=" in indicator:
                    content = await page.content()
                    text = indicator.replace("text='", "").replace("'", "")
                    if text.lower() in content.lower():
                        return True
                else:
                    element = await page.query_selector(indicator)
                    if element and await element.is_visible():
                        return True
            except Exception:
                continue
        
        return False

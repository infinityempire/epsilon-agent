"""Async Playwright browser automation for account creation."""
import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

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

# ---------------------------------------------------------------------------
# Platform registry: maps hostname → handler method name
# ---------------------------------------------------------------------------
PLATFORM_HANDLERS = {
    "bsky.app":              "_handle_bluesky",
    "joinmastodon.org":      "_handle_mastodon",
    "join-lemmy.org":        "_handle_lemmy",
    "news.ycombinator.com":  "_handle_hackernews",
    "discord.com":           "_handle_discord",
}


@dataclass
class AutomationResult:
    """Result of an automation execution."""

    status: TaskStatus
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    screenshot_path: Optional[str] = None


class InboxService:
    """Abstract inbox service for receiving confirmation codes (stub)."""

    async def get_verification_code(self, email: str, timeout_seconds: int = 60) -> Optional[str]:
        logger.info(f"Waiting for verification code for {email}")
        await asyncio.sleep(random.uniform(2, 5))
        simulated_code = str(random.randint(100000, 999999))
        logger.info(f"Simulated verification code received: {simulated_code}")
        return simulated_code

    async def setup_inbox(self, email: str) -> bool:
        logger.info(f"Setting up inbox for {email}")
        return True

    async def cleanup_inbox(self, email: str) -> None:
        logger.info(f"Cleaning up inbox for {email}")


class BrowserAgent:
    """Async browser automation agent for account creation."""

    # Browser launch arguments for stealth mode
    BROWSER_ARGS = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-web-security",
        "--disable-features=IsolateOrigins,site-per-process",
    ]

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser_type: Optional[BrowserType] = None
        self._inbox_service = InboxService()
        self._context: Optional[BrowserContext] = None
        self._browser = None
        self._context_count = 0
        self._max_contexts_before_refresh = 10  # Refresh browser after N contexts

    async def initialize(self) -> None:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            self._browser_type = self._playwright.chromium

    async def cleanup(self) -> None:
        """Clean up all browser resources."""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        logger.info("BrowserAgent cleanup complete")

    async def _refresh_context(self) -> None:
        """Create a fresh browser context to avoid state pollution."""
        logger.info("Refreshing browser context")
        
        # Close existing context
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        
        self._context_count += 1
        
        # Periodically refresh the entire browser to avoid memory leaks
        if self._context_count >= self._max_contexts_before_refresh:
            logger.info("Refreshing entire browser")
            if self._browser:
                try:
                    await self._browser.close()
                except Exception:
                    pass
                self._browser = None
            self._context_count = 0

    async def _ensure_context(self) -> BrowserContext:
        """Get or create a browser context with stealth settings."""
        if self._context is None:
            # Launch browser if needed
            if self._browser is None:
                self._browser = await self._browser_type.launch(
                    headless=settings.headless,
                    args=self.BROWSER_ARGS,
                )
            
            # Create context with realistic settings
            self._context = await self._browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=self._get_realistic_user_agent(),
                locale="en-US",
                timezone_id="America/New_York",
                ignore_https_errors=True,
            )
            
            # Apply stealth scripts
            await self._context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                window.chrome = {runtime: {}};
            """)
            
            # Set default timeout
            self._context.set_default_timeout(settings.browser_timeout_ms)
            self._context.set_default_navigation_timeout(settings.navigation_timeout_ms)
            
            logger.info(f"Created new browser context (count: {self._context_count})")
        
        return self._context

    def _get_realistic_user_agent(self) -> str:
        chrome_versions = ["120.0.0.0", "121.0.0.0", "122.0.0.0"]
        return (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{random.choice(chrome_versions)} Safari/537.36"
        )

    async def _check_for_blocking_elements(self, page: Page) -> tuple[bool, str]:
        for selector in CAPTCHA_SELECTORS + BOT_DETECTION_SELECTORS:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    logger.warning(f"Blocking element detected: {selector}")
                    return True, selector
            except Exception:
                continue
        return False, ""

    async def _take_screenshot(self, page: Page, task_id: str, prefix: str = "result") -> str:
        settings.ensure_screenshot_dir()
        timestamp = int(time.time())
        filename = f"{prefix}_{task_id}_{timestamp}.png"
        filepath = settings.screenshot_dir / filename
        await page.screenshot(path=str(filepath), full_page=True)
        logger.info(f"Screenshot saved to {filepath}")
        return str(filepath)

    async def _human_delay(self, min_ms: int = 400, max_ms: int = 1200) -> None:
        await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)

    async def _type(self, page: Page, selector: str, value: str) -> bool:
        """Fill a field identified by selector; returns True on success."""
        # Try each comma-separated selector individually
        for sel in [s.strip() for s in selector.split(",")]:
            try:
                el = await page.wait_for_selector(sel, timeout=4000, state="visible")
                if el:
                    # Use fill() first (works with React controlled inputs)
                    try:
                        await el.fill(value)
                        return True
                    except Exception:
                        pass
                    # Fallback: click + type
                    try:
                        await el.click()
                        await page.keyboard.press("Control+a")
                        await el.type(value, delay=random.uniform(40, 80))
                        return True
                    except Exception:
                        pass
            except Exception:
                continue
        return False

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------
    async def execute_signup(
        self,
        task_id: str,
        request: SignupRequest,
        session_id: Optional[str] = None,
        export_session: bool = False,
    ) -> AutomationResult:
        """
        Execute signup/login flow for a given request.
        
        Args:
            task_id: Unique task identifier
            request: SignupRequest with credentials
            session_id: Optional session ID to restore (for distributed workers)
            export_session: If True, export session after successful login
            
        Returns:
            AutomationResult with status and any error details
        """
        page: Optional[Page] = None
        platform_name = "unknown"
        try:
            await self.initialize()
            await self._inbox_service.setup_inbox(request.email)
            context = await self._ensure_context()
            
            # Import session if provided (for distributed worker session reuse)
            if session_id:
                from app.storage import session_manager
                if await session_manager.import_session(platform_name, session_id, context):
                    logger.info(f"Restored session {session_id}")
            
            page = await context.new_page()

            target_url = str(request.target_url) if request.target_url else settings.mock_target_url
            hostname = urlparse(target_url).hostname or ""

            # Detect platform from hostname
            platform_name = self._detect_platform(hostname)
            logger.info(f"[{platform_name.upper()}] Processing task {task_id}")

            # Re-import session with correct platform after detection
            if session_id:
                from app.storage import session_manager
                await session_manager.import_session(platform_name, session_id, context)

            # Resolve platform-specific handler
            handler_name = None
            for domain, hname in PLATFORM_HANDLERS.items():
                if domain in hostname:
                    handler_name = hname
                    break

            if handler_name:
                handler = getattr(self, handler_name)
                result = await handler(page, task_id, request)
            else:
                # Fallback: generic flow
                result = await self._generic_signup(page, task_id, request, target_url)

            # Export session if requested and login was successful
            if export_session and result.status == TaskStatus.COMPLETED:
                try:
                    from app.storage import session_manager
                    new_session_id = await session_manager.export_session(
                        platform_name,
                        context,
                        metadata={
                            "task_id": task_id,
                            "email": request.email,
                            "platform": platform_name,
                        }
                    )
                    logger.info(f"Exported session {new_session_id} for {platform_name}")
                    if result.result:
                        result.result["session_id"] = new_session_id
                except Exception as e:
                    logger.warning(f"Failed to export session: {e}")

            await self._inbox_service.cleanup_inbox(request.email)
            return result

        except PlaywrightTimeout as e:
            try:
                screenshot_path = await self._take_screenshot(page, task_id, "timeout") if page else None
            except Exception:
                screenshot_path = None
            await self._inbox_service.cleanup_inbox(request.email)
            return AutomationResult(
                status=TaskStatus.FAILED,
                error_message=f"Browser operation timed out: {str(e)}",
                screenshot_path=screenshot_path,
            )
        except Exception as e:
            try:
                screenshot_path = await self._take_screenshot(page, task_id, "exception") if page else None
            except Exception:
                screenshot_path = None
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

    def _detect_platform(self, hostname: str) -> str:
        """Detect platform from hostname."""
        platform_map = {
            "bsky.app": "bluesky",
            "joinmastodon.org": "mastodon",
            "mastodon.social": "mastodon",
            "join-lemmy.org": "lemmy",
            "lemmy.world": "lemmy",
            "news.ycombinator.com": "hackernews",
            "discord.com": "discord",
        }
        for domain, platform in platform_map.items():
            if domain in hostname:
                return platform
        return "generic"

    # -----------------------------------------------------------------------
    # Platform handlers
    # -----------------------------------------------------------------------

    # --- Bluesky -----------------------------------------------------------
    async def _handle_bluesky(self, page: Page, task_id: str, request: SignupRequest) -> AutomationResult:
        """
        Bluesky login via https://bsky.app
        Flow: navigate → dismiss modal → wait for SPA → fill identifier + password → click Sign in
        The sign-in form is a React SPA; we wait for the input to appear.
        """
        logger.info("[Bluesky] Starting login flow")
        try:
            await page.goto("https://bsky.app", wait_until="domcontentloaded", timeout=30000)
            await self._human_delay(2000, 4000)

            # Bluesky shows a welcome modal with a 'Sign in' link.
            # The modal has aria-modal=true and intercepts pointer events.
            # First, dismiss any modal dialog by clicking outside or pressing Escape
            try:
                # Try pressing Escape to close modal
                await page.keyboard.press("Escape")
                await self._human_delay(500, 1000)
            except Exception:
                pass

            # Try to find and click a dismiss/close button
            try:
                dismissed = await page.evaluate("""
                    () => {
                        // Try clicking backdrop to dismiss modal
                        const backdrops = document.querySelectorAll('[data-testid*="backdrop"], .Overlay-backdrop, [class*="backdrop"]');
                        for (const el of backdrops) {
                            if (el.offsetParent !== null) {
                                el.click();
                                return 'backdrop';
                            }
                        }
                        // Try clicking close button
                        const closeBtns = document.querySelectorAll('[aria-label="Close"], [data-testid="closeModal"], button[class*="close"]');
                        for (const btn of closeBtns) {
                            if (btn.offsetParent !== null) {
                                btn.click();
                                return 'close-btn';
                            }
                        }
                        return null;
                    }
                """)
                if dismissed:
                    logger.info(f"[Bluesky] Dismissed modal via: {dismissed}")
                    await self._human_delay(1000, 2000)
            except Exception:
                pass  # Modal dismissal attempted

            # Now try to click Sign in link using JS dispatch
            try:
                await page.evaluate("""
                    () => {
                        // Try multiple strategies to find Sign in button
                        const strategies = [
                            // Direct text match
                            () => Array.from(document.querySelectorAll('a, button')).find(el => el.textContent.trim() === 'Sign in'),
                            // aria-label match
                            () => document.querySelector('[aria-label*="Sign in" i]'),
                            // data-testid match
                            () => document.querySelector('[data-testid*="signin" i]'),
                        ];
                        for (const strat of strategies) {
                            const el = strat();
                            if (el && el.offsetParent !== null) {
                                el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                                return true;
                            }
                        }
                        return false;
                    }
                """)
                await self._human_delay(2000, 3000)
            except Exception:
                pass  # Sign in navigation attempted

            # If still on main page, navigate directly to login
            current_url = page.url
            if "login" not in current_url:
                await page.goto("https://bsky.app/login", wait_until="domcontentloaded", timeout=30000)
                await self._human_delay(2000, 3000)

            # Wait for the identifier input to appear (SPA may need time)
            try:
                await page.wait_for_selector(
                    "input[data-testid='loginUsernameInput'], input[placeholder*='username' i], input[placeholder*='handle' i], input[autocomplete='username'], input[type='text']",
                    timeout=15000, state="visible"
                )
            except PlaywrightTimeout:
                sp = await self._take_screenshot(page, task_id, "bluesky_no_input")
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message="Bluesky: login form inputs not found", screenshot_path=sp)

            # Identifier field - extract handle from email
            handle = request.email.split("@")[0] if "@" in request.email else request.email
            filled_id = await self._type(
                page,
                "input[data-testid='loginUsernameInput'], input[placeholder*='username' i], input[placeholder*='handle' i], input[autocomplete='username'], input[type='text']",
                handle + ".bsky.social"
            )
            if not filled_id:
                # Try with just the handle
                await self._type(page, "input[type='text']", handle)
            await self._human_delay(500, 900)

            # Password field
            await self._type(page, "input[type='password'], input[data-testid='loginPasswordInput']", request.password)
            await self._human_delay(500, 900)

            # Submit — use JS click to bypass any overlay
            submitted = await page.evaluate("""
                () => {
                    const submitBtn = document.querySelector("button[data-testid='loginSubmitButton'], button[type='submit']");
                    if (submitBtn) {
                        submitBtn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        return true;
                    }
                    return false;
                }
            """)
            if not submitted:
                await page.keyboard.press("Enter")
            await self._human_delay(4000, 7000)

            blocked, sel = await self._check_for_blocking_elements(page)
            if blocked:
                sp = await self._take_screenshot(page, task_id, "bluesky_blocked")
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"Bluesky: blocking element {sel}", screenshot_path=sp)

            sp = await self._take_screenshot(page, task_id, "bluesky_done")
            url_after = page.url
            content = await page.content()
            # Success: redirected away from login page, or feed/home indicators present
            if ("bsky.app" in url_after and "login" not in url_after) or \
               any(kw in content.lower() for kw in ["home", "feed", "following", "notifications", "profile"]):
                logger.info(f"[Bluesky] Login successful, URL: {url_after}")
                return AutomationResult(status=TaskStatus.COMPLETED,
                                        result={"platform": "bluesky", "email": request.email,
                                                "url_after": url_after},
                                        screenshot_path=sp)
            else:
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"Bluesky: still on sign-in page after submit: {url_after}",
                                        screenshot_path=sp)
        except PlaywrightTimeout as e:
            sp = await self._take_screenshot(page, task_id, "bluesky_timeout")
            return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                    error_message=f"Bluesky: timeout (possible CAPTCHA or anti-bot): {e}", screenshot_path=sp)
        except Exception as e:
            sp = await self._take_screenshot(page, task_id, "bluesky_error")
            return AutomationResult(status=TaskStatus.FAILED,
                                    error_message=f"Bluesky error: {e}", screenshot_path=sp)

    # --- Mastodon ----------------------------------------------------------
    async def _handle_mastodon(self, page: Page, task_id: str, request: SignupRequest) -> AutomationResult:
        """
        Mastodon: joinmastodon.org is a directory page.
        We attempt login on mastodon.social (the flagship instance).
        """
        logger.info("[Mastodon] Starting login flow on mastodon.social")
        try:
            # Navigate to login page
            await page.goto("https://mastodon.social/auth/sign_in", wait_until="domcontentloaded", timeout=30000)
            await self._human_delay(2000, 3000)

            # Wait for form inputs to be visible
            try:
                await page.wait_for_selector(
                    "input[name='user[email]'], input[type='email'], input[id='user_email']",
                    timeout=10000, state="visible"
                )
            except PlaywrightTimeout:
                # Try home page redirect
                await page.goto("https://mastodon.social/", wait_until="domcontentloaded", timeout=30000)
                await self._human_delay(2000, 3000)
                # Click login link if present
                await page.evaluate("""
                    () => {
                        const loginLink = Array.from(document.querySelectorAll('a')).find(el => 
                            el.textContent.toLowerCase().includes('log in') || 
                            el.href?.includes('auth/sign_in')
                        );
                        if (loginLink) loginLink.click();
                    }
                """)
                await self._human_delay(2000, 3000)

            # Fill email field using multiple strategies
            email_filled = await self._type(
                page,
                "input[name='user[email]'], input[id='user_email'], input[type='email']",
                request.email
            )
            if not email_filled:
                await self._type(page, "input[type='text']", request.email)
            await self._human_delay(400, 700)

            # Fill password field
            password_filled = await self._type(
                page,
                "input[name='user[password]'], input[id='user_password'], input[type='password']",
                request.password
            )
            if not password_filled:
                # Try finding password field by label
                await page.evaluate("""
                    () => {
                        const inputs = document.querySelectorAll('input');
                        for (const inp of inputs) {
                            const label = document.querySelector(`label[for='${inp.id}']`);
                            if (label && label.textContent.toLowerCase().includes('password')) {
                                inp.value = arguments[0];
                            }
                        }
                    }
                """, request.password)
            await self._human_delay(400, 700)

            # Submit using JavaScript to ensure it fires
            submitted = await page.evaluate("""
                () => {
                    const form = document.querySelector('form[action*="sign_in"]');
                    const button = document.querySelector("button[type='submit'], input[type='submit']");
                    if (form) {
                        form.submit();
                        return 'form';
                    } else if (button) {
                        button.click();
                        return 'button';
                    }
                    return null;
                }
            """)
            if not submitted:
                await page.keyboard.press("Enter")
            
            # Wait for navigation after submission
            await self._human_delay(4000, 6000)
            
            # Wait for potential redirect
            try:
                await page.wait_for_url(lambda url: "sign_in" not in url, timeout=5000)
            except Exception:
                pass  # May still be on sign_in page

            blocked, sel = await self._check_for_blocking_elements(page)
            if blocked:
                sp = await self._take_screenshot(page, task_id, "mastodon_blocked")
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"Mastodon: blocking element {sel}", screenshot_path=sp)

            sp = await self._take_screenshot(page, task_id, "mastodon_done")
            url_after = page.url
            content = await page.content()
            
            # Success indicators
            success_indicators = ["home", "timeline", "notifications", "profile", "logged_in", "logout"]
            is_logged_in = any(ind in content.lower() for ind in success_indicators)
            
            if "sign_in" not in url_after and ("mastodon.social" in url_after or is_logged_in):
                logger.info(f"[Mastodon] Login successful, URL: {url_after}")
                return AutomationResult(status=TaskStatus.COMPLETED,
                                        result={"platform": "mastodon", "email": request.email,
                                                "url_after": url_after},
                                        screenshot_path=sp)
            else:
                # Check for error messages on the page
                error_indicators = ["invalid", "incorrect", "wrong", "error", "failed"]
                has_error = any(err in content.lower() for err in error_indicators)
                if has_error:
                    return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                            error_message=f"Mastodon: login failed - check credentials",
                                            screenshot_path=sp)
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"Mastodon: unexpected URL after login: {url_after}",
                                        screenshot_path=sp)
        except PlaywrightTimeout as e:
            sp = await self._take_screenshot(page, task_id, "mastodon_timeout")
            return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                    error_message=f"Mastodon: timeout (possible CAPTCHA or anti-bot): {e}", screenshot_path=sp)
        except Exception as e:
            sp = await self._take_screenshot(page, task_id, "mastodon_error")
            return AutomationResult(status=TaskStatus.FAILED,
                                    error_message=f"Mastodon error: {e}", screenshot_path=sp)

    # --- Lemmy -------------------------------------------------------------
    async def _handle_lemmy(self, page: Page, task_id: str, request: SignupRequest) -> AutomationResult:
        """
        Lemmy: join-lemmy.org is a directory. We login on lemmy.world (largest instance).
        The login form has 'Email or Username' and 'Password' inputs plus a 'Login' button.
        """
        logger.info("[Lemmy] Starting login flow on lemmy.world")
        try:
            await page.goto("https://lemmy.world/login", wait_until="networkidle", timeout=30000)
            await self._human_delay(1500, 2500)

            # Fill email/username — use fill() directly on the first visible text input
            inputs = await page.query_selector_all("input[type='text'], input[type='email'], input:not([type='password'])")
            filled_user = False
            for inp in inputs:
                try:
                    if await inp.is_visible():
                        await inp.click()
                        await page.keyboard.press("Control+a")
                        await inp.type(request.email, delay=random.uniform(40, 100))
                        filled_user = True
                        break
                except Exception:
                    continue
            if not filled_user:
                await self._type(page, "input", request.email)
            await self._human_delay(400, 700)

            # Password
            await self._type(page, "input[type='password']", request.password)
            await self._human_delay(400, 700)

            # Click the Login button using JavaScript to bypass visibility issues
            clicked = await page.evaluate("""
                () => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const login = btns.find(b => b.textContent.trim().toLowerCase() === 'login');
                    if (login) { login.click(); return true; }
                    return false;
                }
            """)
            if not clicked:
                await page.keyboard.press("Enter")
            await self._human_delay(3000, 5000)

            blocked, sel = await self._check_for_blocking_elements(page)
            if blocked:
                sp = await self._take_screenshot(page, task_id, "lemmy_blocked")
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"Lemmy: blocking element {sel}", screenshot_path=sp)

            sp = await self._take_screenshot(page, task_id, "lemmy_done")
            url_after = page.url
            content = await page.content()
            if "login" not in url_after or any(kw in content.lower() for kw in ["profile", "inbox", "logout"]):
                return AutomationResult(status=TaskStatus.COMPLETED,
                                        result={"platform": "lemmy", "email": request.email,
                                                "url_after": url_after},
                                        screenshot_path=sp)
            else:
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"Lemmy: still on login page: {url_after}",
                                        screenshot_path=sp)
        except Exception as e:
            sp = await self._take_screenshot(page, task_id, "lemmy_error")
            return AutomationResult(status=TaskStatus.FAILED,
                                    error_message=f"Lemmy error: {e}", screenshot_path=sp)

    # --- Hacker News -------------------------------------------------------
    async def _handle_hackernews(self, page: Page, task_id: str, request: SignupRequest) -> AutomationResult:
        """
        Hacker News login via https://news.ycombinator.com/login
        Fields: acct (username, NOT email), pw (password)
        HN does not use email for login — only the username registered at signup.
        """
        logger.info("[HackerNews] Starting login flow")
        
        # HN login uses the account handle (not email)
        # Try username field first, fall back to extracting from email
        username = request.username if request.username else request.email.split("@")[0]
        # Normalise: HN usernames are lowercase, no special chars, max 15 chars
        username = re.sub(r'[^a-zA-Z0-9_-]', '', username).lower()[:15]
        
        # For testing, if credentials don't match actual HN account, note that
        logger.info(f"[HackerNews] Using username: {username}")
        
        try:
            await page.goto("https://news.ycombinator.com/login", wait_until="domcontentloaded", timeout=30000)
            await self._human_delay(1500, 2500)

            # Wait for the form to be visible
            try:
                await page.wait_for_selector("input[name='acct']", timeout=10000, state="visible")
            except PlaywrightTimeout:
                sp = await self._take_screenshot(page, task_id, "hn_no_form")
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message="HackerNews: login form not found",
                                        screenshot_path=sp)

            # HN login form: input[name='acct'] for username, input[name='pw'] for password
            # Find all inputs to avoid the create-account form
            acct_inputs = await page.query_selector_all("input[name='acct']")
            pw_inputs = await page.query_selector_all("input[name='pw']")

            # Use the FIRST acct/pw pair (login form, not create-account form)
            if acct_inputs:
                try:
                    await acct_inputs[0].click()
                    await page.keyboard.press("Control+a")
                    await page.keyboard.type(username, delay=random.uniform(40, 100))
                except Exception:
                    # Fallback to fill
                    await acct_inputs[0].fill(username)
            await self._human_delay(400, 700)

            if pw_inputs:
                try:
                    await pw_inputs[0].click()
                    await page.keyboard.press("Control+a")
                    await page.keyboard.type(request.password, delay=random.uniform(40, 100))
                except Exception:
                    # Fallback to fill
                    await pw_inputs[0].fill(request.password)
            await self._human_delay(400, 700)

            # Click the login submit button (first input[type=submit] in the login section)
            submits = await page.query_selector_all("input[type='submit']")
            if submits:
                try:
                    await submits[0].click()
                except Exception:
                    # Fallback to Enter key
                    await page.keyboard.press("Enter")
            else:
                await page.keyboard.press("Enter")
            
            await self._human_delay(3000, 5000)

            # Wait for potential redirect
            try:
                await page.wait_for_url(lambda url: "login" not in url, timeout=5000)
            except Exception:
                pass

            blocked, sel = await self._check_for_blocking_elements(page)
            if blocked:
                sp = await self._take_screenshot(page, task_id, "hn_blocked")
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"HN: blocking element {sel}", screenshot_path=sp)

            sp = await self._take_screenshot(page, task_id, "hn_done")
            url_after = page.url
            content = await page.content()
            
            # Check for login errors
            error_patterns = ["Bad login", "bad login", "Wrong", "wrong", "Invalid", "invalid", "failed"]
            has_error = any(pattern.lower() in content.lower() for pattern in error_patterns)
            
            if has_error or "Bad login" in content:
                logger.warning(f"[HackerNews] Login failed for user {username}")
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"HackerNews: bad credentials for username '{username}' (note: HN uses username not email)",
                                        screenshot_path=sp)
            
            if "login" in url_after:
                # Still on login page - might be an error
                if any(ind in content.lower() for ind in ["error", "wrong", "bad"]):
                    return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                            error_message=f"HackerNews: login failed for username '{username}'",
                                            screenshot_path=sp)
                # May be waiting for something (2FA, email confirmation)
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"HackerNews: still on login page after submission: {url_after}",
                                        screenshot_path=sp)
            
            logger.info(f"[HackerNews] Login successful, URL: {url_after}")
            return AutomationResult(status=TaskStatus.COMPLETED,
                                    result={"platform": "hackernews", "username": username,
                                            "url_after": url_after},
                                    screenshot_path=sp)
        except PlaywrightTimeout as e:
            sp = await self._take_screenshot(page, task_id, "hn_timeout")
            return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                    error_message=f"HackerNews: timeout (possible CAPTCHA or anti-bot): {e}", screenshot_path=sp)
        except Exception as e:
            sp = await self._take_screenshot(page, task_id, "hn_error")
            return AutomationResult(status=TaskStatus.FAILED,
                                    error_message=f"HackerNews error: {e}", screenshot_path=sp)

    # --- Discord -----------------------------------------------------------
    async def _handle_discord(self, page: Page, task_id: str, request: SignupRequest) -> AutomationResult:
        """
        Discord login via https://discord.com/login
        Discord's React SPA uses dynamic class names; we target by aria-label.
        Fields: 'Email or Phone Number' (aria-label), 'Password' (aria-label)
        """
        logger.info("[Discord] Starting login flow")
        try:
            await page.goto("https://discord.com/login", wait_until="domcontentloaded", timeout=30000)
            await self._human_delay(2500, 4000)

            # Wait for the email input to appear
            try:
                await page.wait_for_selector(
                    "input[aria-label='Email or Phone Number'], input[name='email'], input[type='email']",
                    timeout=12000, state="visible"
                )
            except Exception:
                pass

            # Fill email
            filled = await self._type(
                page,
                "input[aria-label='Email or Phone Number'], input[name='email'], input[type='email']",
                request.email
            )
            if not filled:
                # Fallback: first visible text input
                inputs = await page.query_selector_all("input:not([type='password'])")
                for inp in inputs:
                    if await inp.is_visible():
                        await inp.click()
                        await page.keyboard.press("Control+a")
                        await inp.type(request.email, delay=random.uniform(40, 100))
                        break
            await self._human_delay(600, 1000)

            # Fill password
            await self._type(
                page,
                "input[aria-label='Password'], input[type='password'], input[name='password']",
                request.password
            )
            await self._human_delay(600, 1000)

            # Click Log In button
            clicked = await page.evaluate("""
                () => {
                    const btns = Array.from(document.querySelectorAll('button[type="submit"]'));
                    if (btns.length > 0) { btns[0].click(); return true; }
                    const all = Array.from(document.querySelectorAll('button'));
                    const li = all.find(b => /log.?in/i.test(b.textContent));
                    if (li) { li.click(); return true; }
                    return false;
                }
            """)
            if not clicked:
                await page.keyboard.press("Enter")
            await self._human_delay(5000, 8000)

            blocked, sel = await self._check_for_blocking_elements(page)
            if blocked:
                sp = await self._take_screenshot(page, task_id, "discord_blocked")
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"Discord: blocking element {sel}", screenshot_path=sp)

            sp = await self._take_screenshot(page, task_id, "discord_done")
            url_after = page.url
            content = await page.content()
            # Discord redirects to /channels/@me on success
            if "channels" in url_after or "@me" in url_after:
                return AutomationResult(status=TaskStatus.COMPLETED,
                                        result={"platform": "discord", "email": request.email,
                                                "url_after": url_after},
                                        screenshot_path=sp)
            elif "login" in url_after:
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"Discord: still on login page — possible CAPTCHA or bad credentials",
                                        screenshot_path=sp)
            else:
                # Intermediate state (e.g., 2FA prompt)
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"Discord: intermediate state after login: {url_after}",
                                        screenshot_path=sp)
        except PlaywrightTimeout as e:
            try:
                sp = await self._take_screenshot(page, task_id, "discord_timeout")
            except Exception:
                sp = None
            return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                    error_message=f"Discord: timeout (likely CAPTCHA/anti-bot): {e}",
                                    screenshot_path=sp)
        except Exception as e:
            try:
                sp = await self._take_screenshot(page, task_id, "discord_error")
            except Exception:
                sp = None
            return AutomationResult(status=TaskStatus.FAILED,
                                    error_message=f"Discord error: {e}", screenshot_path=sp)

    # -----------------------------------------------------------------------
    # Generic fallback
    # -----------------------------------------------------------------------
    async def _generic_signup(self, page: Page, task_id: str, request: SignupRequest, target_url: str) -> AutomationResult:
        try:
            await page.goto(target_url, timeout=settings.navigation_timeout_ms, wait_until="domcontentloaded")
            await self._human_delay(1000, 3000)
            blocked, sel = await self._check_for_blocking_elements(page)
            if blocked:
                sp = await self._take_screenshot(page, task_id, "blocked")
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"CAPTCHA/block detected: {sel}", screenshot_path=sp)
            await self._fill_signup_form(page, request)
            await self._human_delay(500, 1500)
            submit = await page.query_selector(
                "button[type='submit'], input[type='submit'], button:has-text('Sign up'), button:has-text('Create'), button:has-text('Register')"
            )
            if submit:
                await submit.click()
                await self._human_delay(2000, 5000)
            sp = await self._take_screenshot(page, task_id, "generic_done")
            return AutomationResult(status=TaskStatus.COMPLETED,
                                    result={"email": request.email, "username": request.username},
                                    screenshot_path=sp)
        except Exception as e:
            sp = await self._take_screenshot(page, task_id, "generic_error")
            return AutomationResult(status=TaskStatus.FAILED,
                                    error_message=f"Generic error: {e}", screenshot_path=sp)

    async def _fill_signup_form(self, page: Page, request: SignupRequest) -> None:
        field_mappings = {
            "email": ["input[name='email']", "input[id='email']", "input[type='email']", "input[placeholder*='email' i]"],
            "username": ["input[name='username']", "input[id='username']", "input[placeholder*='username' i]"],
            "password": ["input[name='password']", "input[id='password']", "input[type='password']"],
            "confirm_password": ["input[name='confirmPassword']", "input[name='confirm_password']", "input[name='passwordConfirm']", "input[placeholder*='confirm' i]"],
        }
        for field, selectors in field_mappings.items():
            value = request.email if field == "email" else (request.username if field == "username" else request.password)
            for sel in selectors:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        await el.fill("")
                        await el.type(value, delay=random.uniform(50, 120))
                        await self._human_delay(300, 700)
                        break
                except Exception:
                    continue

    async def _handle_verification(self, page: Page, request: SignupRequest) -> Optional[AutomationResult]:
        verification_selectors = [
            "input[name='code']", "input[name='verificationCode']", "input[name='otp']",
            "input[id='verification-code']", "input[placeholder*='code' i]",
            "input[maxlength='6']", "input[maxlength='4']",
        ]
        for selector in verification_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    code = await self._inbox_service.get_verification_code(request.email, timeout_seconds=120)
                    if code:
                        await element.fill("")
                        await element.type(code, delay=random.uniform(50, 150))
                        verify_button = await page.query_selector(
                            "button:has-text('Verify'), button:has-text('Confirm'), button:has-text('Submit')"
                        )
                        if verify_button:
                            await verify_button.click()
                        await self._human_delay(2000, 4000)
                    break
            except Exception:
                continue
        return None

    async def _check_success(self, page: Page) -> bool:
        success_indicators = [
            "[class*='success']", "[class*='confirmed']", "[class*='welcome']",
            "text='Account created'", "text='Welcome'", "text='Verified'",
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

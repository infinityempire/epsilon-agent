"""Async Playwright browser automation for account creation."""
import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass
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

    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser_type: Optional[BrowserType] = None
        self._inbox_service = InboxService()
        self._context: Optional[BrowserContext] = None

    async def initialize(self) -> None:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
            self._browser_type = self._playwright.chromium

    async def cleanup(self) -> None:
        if self._context:
            await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def _ensure_context(self) -> BrowserContext:
        if self._context is None:
            browser = await self._browser_type.launch(
                headless=settings.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            self._context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=self._get_realistic_user_agent(),
                locale="en-US",
            )
            await self._context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
            """)
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
    async def execute_signup(self, task_id: str, request: SignupRequest) -> AutomationResult:
        page: Optional[Page] = None
        try:
            await self.initialize()
            await self._inbox_service.setup_inbox(request.email)
            context = await self._ensure_context()
            page = await context.new_page()

            target_url = str(request.target_url) if request.target_url else settings.mock_target_url
            hostname = urlparse(target_url).hostname or ""

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

    # -----------------------------------------------------------------------
    # Platform handlers
    # -----------------------------------------------------------------------

    # --- Bluesky -----------------------------------------------------------
    async def _handle_bluesky(self, page: Page, task_id: str, request: SignupRequest) -> AutomationResult:
        """
        Bluesky login via https://bsky.app
        Flow: navigate → wait for SPA → fill identifier + password → click Sign in
        The sign-in form is a React SPA; we wait for the input to appear.
        """
        logger.info("[Bluesky] Starting login flow")
        try:
            await page.goto("https://bsky.app", wait_until="domcontentloaded", timeout=30000)
            await self._human_delay(2000, 3000)

            # Bluesky shows a welcome modal with a 'Sign in' link.
            # The modal has aria-modal=true and intercepts pointer events.
            # We use dispatchEvent to bypass pointer-event blocking.
            try:
                await page.wait_for_selector(
                    "a:has-text('Sign in'), button:has-text('Sign in')",
                    timeout=10000
                )
                await page.evaluate("""
                    () => {
                        const all = Array.from(document.querySelectorAll('a, button'));
                        const si = all.find(el => el.textContent.trim() === 'Sign in');
                        if (si) {
                            si.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                        }
                    }
                """)
                await self._human_delay(2000, 3000)
            except Exception:
                pass  # Already on form

            # Wait for the identifier input to appear (SPA may need time)
            try:
                await page.wait_for_selector(
                    "input[data-testid='loginUsernameInput'], input[placeholder*='username' i], input[placeholder*='handle' i], input[autocomplete='username']",
                    timeout=10000, state="visible"
                )
            except Exception:
                pass

            # Identifier field
            filled_id = await self._type(
                page,
                "input[data-testid='loginUsernameInput'], input[placeholder*='username' i], input[placeholder*='handle' i], input[autocomplete='username']",
                request.email
            )
            if not filled_id:
                # Try generic text input
                await self._type(page, "input[type='text']:visible", request.email)
            await self._human_delay(500, 900)

            # Password field
            await self._type(page, "input[type='password'], input[data-testid='loginPasswordInput']", request.password)
            await self._human_delay(500, 900)

            # Submit — prefer data-testid, then text match
            submit = await page.query_selector(
                "button[data-testid='loginSubmitButton'], button:has-text('Sign in')"
            )
            if submit:
                await submit.click()
            else:
                await page.keyboard.press("Enter")
            await self._human_delay(4000, 6000)

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
               any(kw in content.lower() for kw in ["home", "feed", "following", "notifications"]):
                logger.info(f"[Bluesky] Login successful, URL: {url_after}")
                return AutomationResult(status=TaskStatus.COMPLETED,
                                        result={"platform": "bluesky", "email": request.email,
                                                "url_after": url_after},
                                        screenshot_path=sp)
            else:
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"Bluesky: still on sign-in page after submit: {url_after}",
                                        screenshot_path=sp)
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
            await page.goto("https://mastodon.social/auth/sign_in", wait_until="domcontentloaded", timeout=30000)
            await self._human_delay(1500, 2500)

            await self._type(page, "input#user_email, input[name='user[email]'], input[type='email']", request.email)
            await self._human_delay(400, 700)
            await self._type(page, "input#user_password, input[name='user[password]'], input[type='password']", request.password)
            await self._human_delay(400, 700)

            submit = await page.query_selector("button[type='submit'], input[type='submit'], button:has-text('Log in')")
            if submit:
                await submit.click()
            await self._human_delay(3000, 5000)

            blocked, sel = await self._check_for_blocking_elements(page)
            if blocked:
                sp = await self._take_screenshot(page, task_id, "mastodon_blocked")
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"Mastodon: blocking element {sel}", screenshot_path=sp)

            sp = await self._take_screenshot(page, task_id, "mastodon_done")
            url_after = page.url
            if "sign_in" not in url_after and "mastodon.social" in url_after:
                return AutomationResult(status=TaskStatus.COMPLETED,
                                        result={"platform": "mastodon", "email": request.email,
                                                "url_after": url_after},
                                        screenshot_path=sp)
            else:
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"Mastodon: unexpected URL after login: {url_after}",
                                        screenshot_path=sp)
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
        # HN login uses the account handle (not email). The user registered as 'talderie'.
        username = request.username if request.username else request.email.split("@")[0]
        # Normalise: HN usernames are lowercase, no special chars
        username = re.sub(r'[^a-zA-Z0-9_-]', '', username)
        try:
            await page.goto("https://news.ycombinator.com/login", wait_until="domcontentloaded", timeout=30000)
            await self._human_delay(1000, 2000)

            # HN login form: first table row = login section
            # input[name='acct'] for username, input[name='pw'] for password
            acct_inputs = await page.query_selector_all("input[name='acct']")
            pw_inputs   = await page.query_selector_all("input[name='pw']")

            # Use the FIRST acct/pw pair (login form, not create-account form)
            if acct_inputs:
                await acct_inputs[0].click()
                await page.keyboard.press("Control+a")
                await acct_inputs[0].type(username, delay=random.uniform(40, 100))
            await self._human_delay(400, 700)

            if pw_inputs:
                await pw_inputs[0].click()
                await page.keyboard.press("Control+a")
                await pw_inputs[0].type(request.password, delay=random.uniform(40, 100))
            await self._human_delay(400, 700)

            # Click the login submit button (first input[type=submit])
            submits = await page.query_selector_all("input[type='submit']")
            if submits:
                await submits[0].click()
            await self._human_delay(2000, 4000)

            blocked, sel = await self._check_for_blocking_elements(page)
            if blocked:
                sp = await self._take_screenshot(page, task_id, "hn_blocked")
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message=f"HN: blocking element {sel}", screenshot_path=sp)

            sp = await self._take_screenshot(page, task_id, "hn_done")
            url_after = page.url
            content = await page.content()
            if "Bad login" in content or "login" in url_after:
                return AutomationResult(status=TaskStatus.REQUIRES_MANUAL_INTERVENTION,
                                        error_message="HackerNews: bad credentials or login page still shown",
                                        screenshot_path=sp)
            return AutomationResult(status=TaskStatus.COMPLETED,
                                    result={"platform": "hackernews", "username": username,
                                            "url_after": url_after},
                                    screenshot_path=sp)
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

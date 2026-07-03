"""
Stealth login + cookie extraction for all 5 platforms.
Uses playwright-stealth to bypass bot detection.
Saves cookies to app/sessions/<platform>_cookies.json
"""
import asyncio, json, os, random, re
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

EMAIL    = "tal.derie.td@gmail.com"
PASSWORD = "Dog7fr7es!~"
USERNAME = "talderie"

OUT_DIR = Path("/home/ubuntu/epsilon-agent/app/sessions")
OUT_DIR.mkdir(parents=True, exist_ok=True)
SS_DIR  = Path("/home/ubuntu/epsilon-agent/app/screenshots")
SS_DIR.mkdir(parents=True, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")

async def make_page(context):
    page = await context.new_page()
    if HAS_STEALTH:
        await stealth_async(page)
    return page

async def delay(a=0.5, b=1.2):
    await asyncio.sleep(random.uniform(a, b))

async def fill(page, selector, value):
    for sel in [s.strip() for s in selector.split(",")]:
        try:
            el = await page.wait_for_selector(sel, timeout=5000, state="visible")
            if el:
                await el.fill(value)
                return True
        except Exception:
            continue
    return False

async def save_cookies(context, name):
    cookies = await context.cookies()
    path = OUT_DIR / f"{name}_cookies.json"
    path.write_text(json.dumps(cookies, indent=2))
    print(f"  [{name}] Saved {len(cookies)} cookies → {path}")
    return str(path)

async def screenshot(page, name):
    p = SS_DIR / f"{name}_session.png"
    try:
        await page.screenshot(path=str(p), full_page=False, timeout=10000)
    except Exception:
        pass
    return str(p)

# ─── DISCORD ────────────────────────────────────────────────────────────────
async def login_discord(browser):
    ctx = await browser.new_context(user_agent=UA, locale="en-US",
                                    viewport={"width":1280,"height":800})
    page = await make_page(ctx)
    result = {"platform":"discord","status":"FAILED","cookies":None,"screenshot":None}
    try:
        await page.goto("https://discord.com/login", wait_until="domcontentloaded", timeout=60000)
        await delay(3, 4)
        # Wait for React to mount the form
        try:
            await page.wait_for_selector("input[name='email'],input[type='email']", timeout=15000, state="visible")
        except Exception:
            pass
        # Use page.fill() which works reliably with React controlled inputs
        try:
            await page.fill("input[name='email']", EMAIL)
        except Exception:
            await page.fill("input[type='email']", EMAIL)
        await delay(0.5, 1.0)
        await page.fill("input[type='password']", PASSWORD)
        await delay(0.5, 1.0)
        # Click Log In button
        await page.evaluate("""
            () => {
                const btn = document.querySelector('button[type="submit"]');
                if (btn) btn.click();
            }
        """)
        await delay(6, 8)
        url = page.url
        content = await page.content()
        if "channels" in url or "@me" in url:
            result["status"] = "COMPLETED"
        elif "captcha" in content.lower() or "human" in content.lower():
            result["status"] = "CAPTCHA_BLOCKED"
        else:
            result["status"] = "REQUIRES_MANUAL"
        result["cookies"] = await save_cookies(ctx, "discord")
        result["screenshot"] = await screenshot(page, "discord")
    except Exception as e:
        result["error"] = str(e)[:200]
        result["screenshot"] = await screenshot(page, "discord_err")
    finally:
        await ctx.close()
    return result

# ─── BLUESKY ────────────────────────────────────────────────────────────────
async def login_bluesky(browser):
    ctx = await browser.new_context(user_agent=UA, locale="en-US",
                                    viewport={"width":1280,"height":800})
    page = await make_page(ctx)
    result = {"platform":"bluesky","status":"FAILED","cookies":None,"screenshot":None}
    try:
        # Bluesky SPA: navigate to /login route directly
        await page.goto("https://bsky.app/login", wait_until="domcontentloaded", timeout=30000)
        await delay(3, 4)
        # Wait for the identifier input
        try:
            await page.wait_for_selector("input", timeout=10000, state="visible")
        except Exception:
            pass
        # Fill identifier
        try:
            await page.fill("input[data-testid='loginUsernameInput']", EMAIL)
        except Exception:
            try:
                await page.fill("input[autocomplete='username']", EMAIL)
            except Exception:
                inputs = await page.query_selector_all("input:not([type='password'])")
                for inp in inputs:
                    if await inp.is_visible():
                        await inp.fill(EMAIL)
                        break
        await delay(0.5, 0.8)
        # Fill password
        try:
            await page.fill("input[type='password']", PASSWORD)
        except Exception:
            pass
        await delay(0.5, 0.8)
        # Submit
        await page.evaluate("""
            () => {
                const btn = document.querySelector('button[data-testid="loginSubmitButton"]')
                          || [...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Sign in');
                if (btn) btn.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));
            }
        """)
        await delay(5, 7)
        url = page.url
        content = await page.content()
        if "login" not in url and "bsky.app" in url:
            result["status"] = "COMPLETED"
        elif any(k in content.lower() for k in ["home","feed","following","notifications"]):
            result["status"] = "COMPLETED"
        else:
            result["status"] = "REQUIRES_MANUAL"
        result["cookies"] = await save_cookies(ctx, "bluesky")
        result["screenshot"] = await screenshot(page, "bluesky")
    except Exception as e:
        result["error"] = str(e)[:200]
        result["screenshot"] = await screenshot(page, "bluesky_err")
    finally:
        await ctx.close()
    return result

# ─── MASTODON ───────────────────────────────────────────────────────────────
async def login_mastodon(browser):
    ctx = await browser.new_context(user_agent=UA, locale="en-US",
                                    viewport={"width":1280,"height":800})
    page = await make_page(ctx)
    result = {"platform":"mastodon","status":"FAILED","cookies":None,"screenshot":None}
    try:
        await page.goto("https://mastodon.social/auth/sign_in", wait_until="domcontentloaded", timeout=30000)
        await delay(1.5, 2.5)
        await fill(page, "input[name='user[email]'],input[type='email'],input#user_email", EMAIL)
        await delay(0.4, 0.7)
        await fill(page, "input[name='user[password]'],input[type='password'],input#user_password", PASSWORD)
        await delay(0.4, 0.7)
        await page.evaluate("""
            () => {
                const btn = document.querySelector('button[type="submit"],input[type="submit"]');
                if (btn) btn.click();
            }
        """)
        await delay(3, 5)
        url = page.url
        if "sign_in" not in url and "mastodon.social" in url:
            result["status"] = "COMPLETED"
        else:
            result["status"] = "REQUIRES_MANUAL"
        result["cookies"] = await save_cookies(ctx, "mastodon")
        result["screenshot"] = await screenshot(page, "mastodon")
    except Exception as e:
        result["error"] = str(e)[:200]
        result["screenshot"] = await screenshot(page, "mastodon_err")
    finally:
        await ctx.close()
    return result

# ─── LEMMY ──────────────────────────────────────────────────────────────────
async def login_lemmy(browser):
    ctx = await browser.new_context(user_agent=UA, locale="en-US",
                                    viewport={"width":1280,"height":800})
    page = await make_page(ctx)
    result = {"platform":"lemmy","status":"FAILED","cookies":None,"screenshot":None}
    try:
        await page.goto("https://lemmy.world/login", wait_until="networkidle", timeout=30000)
        await delay(1.5, 2.5)
        # Fill email
        inputs = await page.query_selector_all("input:not([type='password'])")
        for inp in inputs:
            if await inp.is_visible():
                await inp.fill(EMAIL)
                break
        await delay(0.4, 0.7)
        # Fill password
        pw = await page.query_selector("input[type='password']")
        if pw:
            await pw.fill(PASSWORD)
        await delay(0.4, 0.7)
        await page.evaluate("""
            () => {
                const btns = [...document.querySelectorAll('button')];
                const login = btns.find(b => b.textContent.trim().toLowerCase() === 'login');
                if (login) login.click();
            }
        """)
        await delay(3, 5)
        url = page.url
        content = await page.content()
        if "login" not in url or any(k in content.lower() for k in ["profile","inbox","logout"]):
            result["status"] = "COMPLETED"
        else:
            result["status"] = "REQUIRES_MANUAL"
        result["cookies"] = await save_cookies(ctx, "lemmy")
        result["screenshot"] = await screenshot(page, "lemmy")
    except Exception as e:
        result["error"] = str(e)[:200]
        result["screenshot"] = await screenshot(page, "lemmy_err")
    finally:
        await ctx.close()
    return result

# ─── HACKER NEWS ────────────────────────────────────────────────────────────
async def login_hn(browser):
    ctx = await browser.new_context(user_agent=UA, locale="en-US",
                                    viewport={"width":1280,"height":800})
    page = await make_page(ctx)
    result = {"platform":"hackernews","status":"FAILED","cookies":None,"screenshot":None}
    try:
        await page.goto("https://news.ycombinator.com/login", wait_until="domcontentloaded", timeout=30000)
        await delay(1, 2)
        accts = await page.query_selector_all("input[name='acct']")
        pws   = await page.query_selector_all("input[name='pw']")
        if accts:
            await accts[0].fill(USERNAME)
        await delay(0.3, 0.6)
        if pws:
            await pws[0].fill(PASSWORD)
        await delay(0.3, 0.6)
        submits = await page.query_selector_all("input[type='submit']")
        if submits:
            await submits[0].click()
        await delay(2, 4)
        url = page.url
        content = await page.content()
        if "Bad login" in content or "login" in url:
            result["status"] = "BAD_CREDENTIALS"
        elif "Sorry" in content:
            result["status"] = "RATE_LIMITED"
        else:
            result["status"] = "COMPLETED"
        result["cookies"] = await save_cookies(ctx, "hackernews")
        result["screenshot"] = await screenshot(page, "hackernews")
    except Exception as e:
        result["error"] = str(e)[:200]
        result["screenshot"] = await screenshot(page, "hn_err")
    finally:
        await ctx.close()
    return result

# ─── MAIN ───────────────────────────────────────────────────────────────────
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1280,800",
            ]
        )
        results = {}
        for name, coro in [
            ("discord",    login_discord(browser)),
            ("bluesky",    login_bluesky(browser)),
            ("mastodon",   login_mastodon(browser)),
            ("lemmy",      login_lemmy(browser)),
            ("hackernews", login_hn(browser)),
        ]:
            print(f"\n{'='*40}\n[{name.upper()}]")
            res = await coro
            results[name] = res
            print(f"  Status: {res['status']}")
            if res.get("error"):
                print(f"  Error:  {res['error'][:120]}")

        await browser.close()

    out = Path("/home/ubuntu/epsilon-agent/session_results.json")
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\n\nAll results → {out}")
    print(json.dumps({k: v["status"] for k,v in results.items()}, indent=2))

if __name__ == "__main__":
    asyncio.run(main())

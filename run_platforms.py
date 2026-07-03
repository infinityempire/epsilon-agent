"""Direct execution script: run login flow for all 5 platforms."""
import asyncio
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.browser_agent import BrowserAgent
from app.schemas import SignupRequest

EMAIL    = "tal.derie.td@gmail.com"
PASSWORD = "Dog7fr7es!~"
USERNAME = "talderie"

PLATFORMS = [
    ("bluesky",     "https://bsky.app"),
    ("mastodon",    "https://joinmastodon.org"),
    ("lemmy",       "https://join-lemmy.org"),
    ("hackernews",  "https://news.ycombinator.com"),
    ("discord",     "https://discord.com/register"),
]

async def run_one(name: str, url: str) -> dict:
    """Run a single platform in its own BrowserAgent instance."""
    agent = BrowserAgent()
    try:
        await agent.initialize()
        req = SignupRequest(
            email=EMAIL,
            password=PASSWORD,
            username=USERNAME,
            target_url=url,
        )
        result = await agent.execute_signup(task_id=name, request=req)
        return {
            "status": str(result.status),
            "result": result.result,
            "error": result.error_message,
            "screenshot": result.screenshot_path,
        }
    except Exception as e:
        return {
            "status": "FAILED",
            "result": None,
            "error": str(e),
            "screenshot": None,
        }
    finally:
        try:
            await agent.cleanup()
        except Exception:
            pass

async def main():
    results = {}
    for name, url in PLATFORMS:
        print(f"\n{'='*50}\n[{name.upper()}] Starting...\n{'='*50}")
        res = await run_one(name, url)
        results[name] = res
        print(f"[{name.upper()}] Status: {res['status']}")
        if res['error']:
            print(f"[{name.upper()}] Error: {res['error'][:200]}")
        if res['screenshot']:
            print(f"[{name.upper()}] Screenshot: {res['screenshot']}")

    print("\n\n" + "="*60)
    print("FINAL RESULTS")
    print("="*60)
    print(json.dumps(results, indent=2, default=str))

    with open("/home/ubuntu/epsilon-agent/platform_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nResults saved to platform_results.json")

if __name__ == "__main__":
    asyncio.run(main())

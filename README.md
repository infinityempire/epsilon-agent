# Epsilon Agent 🤖

> Autonomous AI agent system for account creation with Playwright browser automation

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-green.svg)](https://fastapi.tiangolo.com/)
[![Playwright](https://img.shields.io/badge/Playwright-1.41-yellow.svg)](https://playwright.dev/)

## Overview

Epsilon Agent is a production-ready autonomous AI agent system designed for automated account creation on supported websites. Built with async/await throughout using FastAPI, Playwright (async), and Redis.

### Key Features

- **Async Architecture**: All I/O operations are non-blocking using `async/await`
- **Browser Isolation**: Each task uses a completely isolated Playwright Browser Context
- **Task Queue**: Redis-based task queue with reliable processing
- **CAPTCHA Handling**: Detects and halts on CAPTCHA/bot detection, saving screenshots
- **Email Verification**: Stubbed `InboxService` for simulating OTP/code receipt
- **Resilient**: Proper error handling, timeouts, and retry logic with TTL retention

## Architecture

```
app/
├── main.py          # FastAPI application with endpoints
├── worker.py       # Async worker loop for task processing
├── browser_agent.py # Playwright automation logic
├── storage.py      # Redis storage abstraction
├── config.py       # Pydantic BaseSettings configuration
├── schemas.py      # Request/response validation models
└── screenshots/    # Captured screenshots on failures
```

## Quick Start

### Prerequisites

- Python 3.10+
- Redis server
- Playwright browsers (`playwright install`)

### Installation

```bash
# Clone the repository
git clone https://github.com/infinityempire/epsilon-agent.git
cd epsilon-agent

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium

# Create .env file (optional)
cat > .env << EOF
REDIS_URL=redis://localhost:6379/0
HEADLESS=true
DEBUG=false
EOF
```

### Running

```bash
# Start the API server
uvicorn app.main:app --host 0.0.0.0 --port 8000

# In another terminal, start the worker
python -m app.worker
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/signup` | POST | Create a new signup task |
| `/status/{task_id}` | GET | Get task status |
| `/health` | GET | Health check |
| `/queue/stats` | GET | Queue statistics |

### Example Request

```bash
curl -X POST "http://localhost:8000/signup" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "username": "testuser123",
    "password": "SecureP@ss123"
  }'
```

### Example Response

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PENDING",
  "message": "Signup task created successfully",
  "created_at": "2026-07-03T03:21:10"
}
```

## Task Status Flow

```
PENDING → IN_PROGRESS → COMPLETED
                     → FAILED (retries exhausted)
                     → REQUIRES_MANUAL_INTERVENTION (CAPTCHA detected)
```

## Task Lifecycle

1. **Task Created**: Added to Redis pending queue
2. **Worker Picks Up**: Status changes to `IN_PROGRESS`
3. **Browser Automation**: Playwright navigates, fills form, submits
4. **CAPTCHA Detected**: Screenshot saved, status → `REQUIRES_MANUAL_INTERVENTION`
5. **Success**: Status → `COMPLETED` with result data
6. **Failure**: Retries up to `MAX_RETRIES`, then → `FAILED`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection URL |
| `HEADLESS` | `true` | Run browser in headless mode |
| `BROWSER_TIMEOUT_MS` | `30000` | Browser operation timeout |
| `NAVIGATION_TIMEOUT_MS` | `60000` | Page navigation timeout |
| `WORKER_POLL_INTERVAL` | `1.0` | Worker polling interval (seconds) |
| `MAX_RETRIES` | `3` | Maximum task retry attempts |
| `TASK_TTL_SECONDS` | `86400` | Task data retention TTL |

## Legal Notice

⚠️ **Important**: This software is provided for educational and legitimate automation purposes only. Users must:

- Comply with website Terms of Service
- Not attempt to bypass CAPTCHAs or advanced anti-bot systems
- Use responsibly and ethically
- The agent halts and flags for manual intervention when blocked

## License

MIT License - See [LICENSE](LICENSE) for details.

---

Built with ❤️ by [Epsilon Agent](https://github.com/infinityempire/epsilon-agent)

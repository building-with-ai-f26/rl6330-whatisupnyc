"""Check that each API key works. Prints pass or fail only, never a key."""
import os

import httpx
from dotenv import load_dotenv

load_dotenv()


def report(name: str, send) -> None:
    key_env = f"{name.upper()}_API_KEY"
    key = os.environ.get(key_env)
    if not key:
        print(f"{name}: FAILED (no {key_env} in .env)")
        return
    try:
        status = send(key).status_code
    except httpx.HTTPError as exc:
        print(f"{name}: FAILED ({type(exc).__name__})")
        return
    print(f"{name}: {'OK' if status == 200 else 'FAILED'} (HTTP {status})")


report(
    "openrouter",
    lambda key: httpx.get(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    ),
)
report(
    "ticketmaster",
    lambda key: httpx.get(
        "https://app.ticketmaster.com/discovery/v2/events.json",
        params={"apikey": key, "stateCode": "NY", "size": 1},
        timeout=30,
    ),
)
report(
    "tavily",
    lambda key: httpx.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {key}"},
        json={"query": "NYC events", "max_results": 1},
        timeout=30,
    ),
)
report(
    "minimax",
    lambda key: httpx.get(
        "https://api.minimax.io/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    ),
)

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
STATE_FILE = ROOT_DIR / ".seen_articles.json"
ALERTS_FILE = ROOT_DIR / "alerts.json"

FEEDS = {
    "ESPN FC": "https://www.espn.com/espn/rss/soccer/news",
    "FIFA": "https://www.fifa.com/fifaplus/en/news/rss.xml",
    "BBC Sport Football": "https://feeds.bbci.co.uk/sport/football/rss.xml",
    "Goal.com": "https://www.goal.com/en/news/rss",
    "Transfermarkt News": "https://www.transfermarkt.com/rss/news",
}

KEYWORDS = [
    "Messi",
    "Cristiano Ronaldo",
    "Neymar",
    "Vinicius Junior",
    "Vini Jr",
    "Endrick",
    "Mbappé",
    "Haaland",
    "Bellingham",
    "Lamine Yamal",
    "goal",
    "red card",
    "VAR",
    "penalty",
    "referee mistake",
    "fight",
    "funny moment",
    "fan reaction",
    "controversy",
    "transfer bomb",
    "injury",
    "emotional",
    "celebration",
]

TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
CHECK_INTERVAL_SECONDS = 5 * 60
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "football-monitor/2.0"
VIRAL_SCORE_THRESHOLD = 8.5


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def load_config() -> dict[str, Any]:
    return {
        "telegram_bot_token": os.getenv(TELEGRAM_BOT_TOKEN_ENV, ""),
        "telegram_chat_id": os.getenv(TELEGRAM_CHAT_ID_ENV, ""),
        "openai_api_key": os.getenv(OPENAI_API_KEY_ENV, ""),
        "openai_model": os.getenv(OPENAI_MODEL_ENV, "gpt-4o-mini"),
        "state_file": STATE_FILE,
        "alerts_file": ALERTS_FILE,
    }

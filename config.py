from __future__ import annotations

import os
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
STATE_FILE = ROOT_DIR / ".seen_articles.json"
ALERTS_FILE = ROOT_DIR / "alerts.json"

def build_youtube_feed_url(channel_id: str) -> str:
    channel_id = (channel_id or "").strip()
    if not channel_id:
        return ""
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def get_feeds() -> dict[str, str]:
    feeds: dict[str, str] = {
        "BBC Sport Football": "https://feeds.bbci.co.uk/sport/football/rss.xml",
        "ESPN FC": "https://www.espn.com/espn/rss/soccer/news",
        "FIFA": "https://www.fifa.com/fifaplus/en/news/rss.xml",
        "Reuters Soccer": "https://www.reutersagency.com/feed/?best-topics=soccer&post_type=best",
    }

    youtube_channel_ids = {
        "FIFA YouTube": os.getenv("FIFA_YOUTUBE_CHANNEL_ID", ""),
        "ESPN FC YouTube": os.getenv("ESPN_YOUTUBE_CHANNEL_ID", ""),
        "CazéTV": os.getenv("CAZETV_YOUTUBE_CHANNEL_ID", "UCZiYbVptd3PVPf4f6eR6UaQ"),
    }

    for source, channel_id in youtube_channel_ids.items():
        feed_url = build_youtube_feed_url(channel_id)
        if feed_url:
            feeds[source] = feed_url

    return feeds


FEEDS = get_feeds()

KEYWORDS = [
    "Messi",
    "Lionel Messi",
    "Cristiano Ronaldo",
    "Neymar",
    "Vinicius Junior",
    "Vini Jr",
    "Endrick",
    "Mbappé",
    "Mbappe",
    "Haaland",
    "Bellingham",
    "Lamine Yamal",
    "goal",
    "golaço",
    "gol",
    "free kick",
    "falta",
    "red card",
    "VAR",
    "penalty",
    "referee mistake",
    "fight",
    "funny moment",
    "fan reaction",
    "controversy",
    "dramatic reaction",
    "briga",
    "confusão",
    "polêmica",
    "árbitro",
    "torcida",
    "reação",
    "engraçado",
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

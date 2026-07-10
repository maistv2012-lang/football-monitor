from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
STATE_FILE = ROOT_DIR / ".seen_articles.json"
ALERTS_FILE = ROOT_DIR / "alerts.json"

# New: Folder for downloaded videos
DOWNLOADS_DIR = ROOT_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

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
    # New keywords for video content prioritization
    "goals World Cup 2026",
    "highlights World Cup 2026",
    "funny moments World Cup 2026",
    "VAR decisions World Cup 2026",
    "red cards World Cup 2026",
    "penalties World Cup 2026",
    "own goals World Cup 2026",
    "interviews World Cup 2026",
    "viral moments World Cup 2026",
]

TELEGRAM_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
CHECK_INTERVAL_SECONDS = 5 * 60
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "football-monitor/2.0"
VIRAL_SCORE_THRESHOLD = 8.5

# New: YouTube search related configurations
YT_DLP_BIN = os.getenv("YT_DLP_BIN", "yt-dlp") # Path to yt-dlp executable
YT_SEARCH_TERMS = [
    "World Cup 2026 goals",
    "World Cup 2026 highlights",
    "World Cup 2026 funny moments",
    "World Cup 2026 VAR",
    "World Cup 2026 red card",
    "World Cup 2026 penalty",
    "World Cup 2026 own goal",
    "World Cup 2026 interview",
    "World Cup 2026 viral",
    "Copa do Mundo 2026 gols",
    "Copa do Mundo 2026 melhores momentos",
    "Copa do Mundo 2026 momentos engraçados",
    "Copa do Mundo 2026 VAR",
    "Copa do Mundo 2026 cartão vermelho",
    "Copa do Mundo 2026 pênalti",
    "Copa do Mundo 2026 gol contra",
    "Copa do Mundo 2026 entrevistas",
    "Copa do Mundo 2026 viral",
]


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def load_config() -> dict[str, Any]:
    raw_debug_mode = os.getenv("FOOTBALL_MONITOR_DEBUG", "0").strip().lower()
    debug_mode = raw_debug_mode in {"1", "true", "yes", "on"}
    enabled = lambda name, default="true": os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}
    try:
        manual_social_sources = json.loads(os.getenv("MANUAL_SOCIAL_SOURCES_JSON", "[]"))
        if not isinstance(manual_social_sources, list):
            manual_social_sources = []
    except json.JSONDecodeError:
        manual_social_sources = []
    return {
        "telegram_bot_token": os.getenv(TELEGRAM_BOT_TOKEN_ENV, ""),
        "telegram_chat_id": os.getenv(TELEGRAM_CHAT_ID_ENV, ""),
        "openai_api_key": os.getenv(OPENAI_API_KEY_ENV, ""),
        "openai_model": os.getenv(OPENAI_MODEL_ENV, "gpt-4o-mini"),
        "state_file": STATE_FILE,
        "alerts_file": ALERTS_FILE,
        "debug_mode": debug_mode,
        "yt_dlp_bin": os.getenv("YT_DLP_BIN", "yt-dlp"),
        "downloads_dir": DOWNLOADS_DIR,
        "yt_search_terms": YT_SEARCH_TERMS,
        "tvnz_auto_download_enabled": os.getenv("TVNZ_AUTO_DOWNLOAD_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"},
        "tvnz_youtube_channel_url": os.getenv("TVNZ_YOUTUBE_CHANNEL_URL", ""),
        "tvnz_backfill_limit": os.getenv("TVNZ_BACKFILL_LIMIT", "5"),
        "monitor_controversies": enabled("MONITOR_CONTROVERSIES"),
        "controversy_first": enabled("CONTROVERSY_FIRST"),
        "brazilian_sources_enabled": enabled("BRAZILIAN_SOURCES_ENABLED"),
        "social_manual_alerts_enabled": enabled("SOCIAL_MANUAL_ALERTS_ENABLED"),
        "instagram_auto_download": enabled("INSTAGRAM_AUTO_DOWNLOAD", "false"),
        "tiktok_auto_download": enabled("TIKTOK_AUTO_DOWNLOAD", "false"),
        "x_auto_download": enabled("X_AUTO_DOWNLOAD", "false"),
        "manual_social_sources": manual_social_sources,
    }

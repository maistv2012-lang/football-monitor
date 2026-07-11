from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path
from typing import Any

import feedparser
import requests
from dotenv import load_dotenv

from config import (
    ALERTS_FILE,
    CHECK_INTERVAL_SECONDS,
    DOWNLOADS_DIR,
    KEYWORDS,
    REQUEST_TIMEOUT_SECONDS,
    STATE_FILE,
    TELEGRAM_BOT_TOKEN_ENV,
    TELEGRAM_CHAT_ID_ENV,
    USER_AGENT,
    VIRAL_SCORE_THRESHOLD,
    YT_DLP_BIN,
    YT_SEARCH_TERMS,
    build_youtube_feed_url,
    get_feeds,
    load_config,
    normalize_text,
)
def build_priority_youtube_queries() -> list[str]:
    priority_teams = [
        "Brazil", "Argentina", "Portugal", "France", "Spain",
        "Colombia", "Mexico", "England", "Germany", "Uruguay",
    ]

    priority_players = [
        "Messi", "Cristiano Ronaldo", "Neymar", "Mbappe",
        "Vini Jr", "Rodrygo", "Endrick", "Lamine Yamal",
        "Luis Diaz", "Bellingham",
    ]

    # More specific and high-value event terms for YouTube search
    event_terms = [
        "goals World Cup 2026",
        "highlights World Cup 2026",
        "funny moments World Cup 2026",
        "VAR decisions World Cup 2026",
        "red cards World Cup 2026",
        "penalties World Cup 2026",
        "own goals World Cup 2026",
        "interviews World Cup 2026",
        "viral moments World Cup 2026",
        "melhores momentos Copa do Mundo 2026",
        "gols Copa do Mundo 2026",
        "momentos engraçados Copa do Mundo 2026",
    ]

    queries = []

    # Combine teams and players with event terms for comprehensive search
    for team in priority_teams:
        for term in event_terms:
            queries.append(f"{team} {term}")

    for player in priority_players:
        for term in event_terms:
            queries.append(f"{player} {term}")

    # Add general high-value search terms
    queries.extend(YT_SEARCH_TERMS)

    return list(dict.fromkeys(queries))


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("football-monitor")
SUBPROCESS_TEXT_KWARGS = {"encoding": "utf-8", "errors": "replace"}

TRUSTED_YOUTUBE_CHANNEL_ORDER = [
    "fifa",
    "fifa+",
    "cazetv",
    "espn fc",
    "bbc sport",
    "sky sports football",
    "premier league",
    "uefa",
    "tnt sports",
    "cbs sports golazo",
    "bein sports",
    "dazn football",
    "tvnz",
    "tvnz+",
    "tvnz sport",
    "sky sport nz",
    "sky sport next",
    "all whites",
    "wellington phoenix",
    "auckland fc",
]
TRUSTED_YOUTUBE_CHANNELS = set(TRUSTED_YOUTUBE_CHANNEL_ORDER)
DOWNLOAD_BLOCKED_CHANNELS = {"cazetv"}
YOUTUBE_DOWNLOAD_CHANNEL = "TVNZ Sport"
DEFAULT_TVNZ_YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@TVNZSport/videos"
FALLBACK_TVNZ_YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@TVNZSport"
TVNZ_YOUTUBE_CHANNEL_ID = "UCY8jpWswn6c3kpaHijtBUAg"
FIFA_YOUTUBE_CHANNEL_ID = "UCpcTrCXblq78GZrTUTLWeBw"
TVNZ_YOUTUBE_RSS_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
RUNNER_LOCATION_URL = "https://ipinfo.io/json"
OFFICIAL_SOURCE_UNAVAILABLE_MESSAGE = (
    "Official highlight found, but no downloadable official source is available for this runner country."
)
MANUAL_LINKS_FILE = Path(__file__).resolve().with_name("manual_links.json")
MANUAL_OPEN_MESSAGE = "Video found. Open manually using the link below."
BOT_VERIFICATION_TERMS = (
    "sign in to confirm you're not a bot", "sign in to confirm you are not a bot",
    "confirm you’re not a bot", "bot verification", "cookies-from-browser",
)
CONTROVERSY_THRESHOLD = 70
CONTROVERSY_KEYWORDS_PT = (
    "polêmica", "lance polêmico", "arbitragem", "erro de arbitragem", "var", "pênalti",
    "pênalti não marcado", "penalti não dado", "gol anulado", "impedimento",
    "impedimento duvidoso", "mão na bola", "falta no lance do gol", "falta antes do gol",
    "expulsão", "cartão vermelho", "entrada criminosa", "confusão", "briga", "reclamação",
    "juiz", "árbitro", "roubo", "vergonhoso",
)
CONTROVERSY_KEYWORDS_EN = (
    "controversy", "controversial", "var", "penalty not given", "penalty denied",
    "disallowed goal", "offside", "handball", "red card", "referee mistake",
    "foul before goal", "controversial tackle", "fight", "protest",
)
HIGH_CONTROVERSY_TERMS = (
    "pênalti não marcado", "penalti não dado", "gol anulado", "falta antes do gol",
    "penalty not given", "penalty denied", "disallowed goal", "red card", "cartão vermelho",
    "referee mistake", "erro de arbitragem", "impedimento duvidoso", "controversial tackle",
)
BRAZILIAN_SPORTS_SOURCES = (
    "Globo Esporte", "ge", "SporTV", "TNT Sports Brasil", "Gazeta Esportiva",
    "Gazeta TV", "CazéTV", "ESPN Brasil",
)
BRAZILIAN_SOURCE_REGISTRY = [
    {"name": name, "category": "brazilian_sports_source", "default_mode": "manual_open_alert"}
    for name in BRAZILIAN_SPORTS_SOURCES
]
DEFAULT_MANUAL_SOCIAL_SOURCES = [
    {"name": "Globo Esporte/ge Instagram", "url": "https://www.instagram.com/ge.globo/"},
    {"name": "SporTV Instagram", "url": "https://www.instagram.com/sportv/"},
    {"name": "TNT Sports Brasil Instagram", "url": "https://www.instagram.com/tntsportsbr/"},
    {"name": "Gazeta Esportiva/Gazeta TV Instagram", "url": "https://www.instagram.com/gazetaesportiva/"},
    {"name": "CazéTV Instagram", "url": "https://www.instagram.com/cazetv/"},
]
OFFICIAL_RSS_ENTRIES_CACHE: dict[str, list[Any]] = {}
PERSISTENT_STATE_FILES = {
    "sent_alerts": "sent_alerts.json",
    "sent_video_ids": "sent_video_ids.json",
    "downloaded_video_ids": "downloaded_video_ids.json",
    "manual_open_links": "manual_open_links.json",
    "skipped_geo_blocked": "skipped_geo_blocked.json",
    "skipped_bot_blocked": "skipped_bot_blocked.json",
    "processed_telegram_shorts": "processed_telegram_shorts.json",
}
PERSISTENT_STATE_CACHE: dict[str, dict[str, dict[str, Any]]] = {}
RUN_DEDUPE_KEYS: set[str] = set()
RESERVED_SEND_IDS: set[str] = set()
TELEGRAM_EDITOR_SESSIONS: dict[str, dict[str, Any]] = {}
TVNZ_SCAN_LIMIT_DEFAULT = 30
TVNZ_MAX_DOWNLOADS_PER_RUN_DEFAULT = 5
TVNZ_HIGHLIGHT_KEYWORDS = (
    "match highlights",
    "highlights",
    "extended highlights",
    "quarter final",
    "round of 16",
    "fifa world cup",
    "world cup",
    "goals",
    "every goal",
    "penalty",
    "penalties",
    "shootout",
    "red card",
    "var",
    "world cup",
    "fifa world cup",
)
TVNZ_REJECTED_VIDEO_TERMS = (
    "interview",
    "press conference",
    "preview",
    "reaction",
    "podcast",
    "live",
    "full match",
    "training",
    "betting",
    "between two goals",
)
YOUTUBE_DOWNLOAD_TITLE_TERMS = ("match highlights", "extended highlights", "highlights")
YOUTUBE_DOWNLOAD_REJECTED_TERMS = (
    "analysis", "reaction", "live", "podcast", "debate", "preview",
    "press conference", "opinion", "heroics", "greatest comeback", "daily",
)
DEFAULT_SHORTS_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/Arial.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/calibri.ttf",
)
DOWNLOAD_ELIGIBLE_TITLE_TERMS = (
    "match highlights", "highlights", "extended highlights", "melhores momentos",
    "resumo do jogo", "todos os gols", "goals", "penalty shootout", "goal",
    "gol", "var", "red card", "save",
)
DOWNLOAD_INELIGIBLE_TITLE_TERMS = (
    "geral cazetv", "live da madrugada", "ao vivo", "aqui e copa", "debate",
    "analise", "opinion", "preview", "podcast", "reacts", "reacao",
    "programa", "tracker", "golden boot", "race", "musings",
)
CAZETV_DISCUSSION_TITLE_TERMS = (
    "geral cazetv", "aqui e copa", "ao vivo", "live da madrugada", "debate",
    "opiniao", "reage", "podcast",
)
CONTENT_DOWNLOAD_CATEGORIES = {
    "MATCH_HIGHLIGHT",
    "GOAL_CLIP",
    "VAR_OR_PENALTY",
    "RED_CARD",
    "SHOOTOUT",
}
CONTENT_ALERT_CATEGORIES = CONTENT_DOWNLOAD_CATEGORIES | {
    "TRANSFER_NEWS",
    "GENERAL_NEWS",
}
MATCH_HIGHLIGHT_TERMS = (
    "melhores momentos", "match highlights", "extended highlights",
    "highlights", "resumo do jogo", "todos os gols",
)
GOAL_CLIP_TERMS = ("gol", "goal", "gols", "goals", "golaco", "equaliser", "winner", "save")
VAR_OR_PENALTY_TERMS = ("var", "penalty", "penalti")
RED_CARD_TERMS = ("red card", "cartao vermelho", "expulso")
SHOOTOUT_TERMS = ("penalty shootout", "shootout")
GENERIC_NEWS_TERMS = (
    "best", "best ever", "greatest", "history", "iconic moments", "moments in history",
    "record breaking", "race", "tracker", "musings", "how to",
    "who will come out on top", "comebacks and upsets", "late goals",
    "goals galore", "compilation", "top 10", "ranking", "top goals",
    "melhores gols", "gols mais bonitos", "mais bonitos", "best moments",
)
DISCUSSION_TERMS = (
    "geral cazetv", "aqui e copa", "debate", "analise", "analysis", "opinion",
    "opiniao", "reacts", "reage", "reacao", "podcast", "preview", "discussion",
    "commentary", "rap", "parody", "gaming", "programa",
)
LIVE_STREAM_TERMS = ("ao vivo", "live da madrugada", "live stream")
TRANSFER_NEWS_TERMS = (
    "signs for", "joins", "agrees deal", "transfer", "transfers", "loan",
    "set to sign", "close to signing", "mercado", "contratacao", "signing",
)

CAZETV_NEWS_FALLBACK_CHANNEL_ORDER = [
    "fifa", "fifa+", "uefa", "premier league", "espn fc", "bbc sport",
    "sky sports football", "tnt sports", "cbs sports golazo", "bein sports",
    "dazn football", "tvnz sport", "sky sport nz",
]

GEO_RESTRICTED_FALLBACK_CHANNEL_ORDER = [
    "fifa",
    "fifa+",
    "tvnz",
    "tvnz sport",
    "sky sport nz",
    "espn fc",
    "bbc sport",
    "uefa",
    "premier league",
    "tnt sports",
    "cbs sports golazo",
    "bein sports",
    "dazn football",
] + [
    channel for channel in TRUSTED_YOUTUBE_CHANNEL_ORDER
    if channel not in {
        "fifa", "fifa+", "tvnz", "tvnz sport", "sky sport nz", "espn fc",
        "bbc sport", "uefa", "premier league", "tnt sports", "cbs sports golazo",
        "bein sports", "dazn football",
    }
]

GEO_RESTRICTION_TERMS = (
    "unavailable in your country",
    "geo restricted",
    "geo-restricted",
    "available in brazil",
    "not available in your country",
)


class GeoRestrictedVideoError(RuntimeError):
    """Raised when an official YouTube upload cannot be accessed in this region."""


class VideoDownloadBlockedError(RuntimeError):
    """Raised when an official download requires manual browser verification."""

UNOFFICIAL_VIDEO_TERMS = {
    "ai generated", "ai-generated", "ai video", "compilation", "fan edit",
    "fan made", "fan-made", "fan reaction", "gaming", "music", "parody",
    "rap", "reaction", "unofficial",
}


def is_relevant_article(article: dict[str, str]) -> bool:
    """Return True when the title contains an important football short-form topic."""
    title = normalize_text(article.get("title", ""))
    if not title:
        return False

    if article.get("source", "") == "CazéTV":
        return any(keyword.lower() in title for keyword in KEYWORDS)

    if any(keyword.lower() in title for keyword in KEYWORDS):
        return True

    low_value_terms = ["training", "coach", "manager", "tactics", "preview", "analysis", "opinion", "interview", "podcast"]
    if any(term in title for term in low_value_terms):
        return False

    high_value_terms = [
        "goal",
        "injury",
        "transfer",
        "bomb",
        "celebration",
        "emotional",
        "controversy",
        "red card",
        "var",
        "penalty",
        "free kick",
        "fan reaction",
        "fight",
        "funny",
        "dramatic reaction",
    ]
    return any(term in title for term in high_value_terms)


def get_source_priority(source: str) -> int:
    """Rank sources from most official to least reliable for discovery workflows."""
    source_text = normalize_text(source)
    if "fifa" in source_text:
        return 1
    if any(token in source_text for token in ["bbc", "sky", "espn", "fox", "tnt", "uefa", "conmebol", "official", "cazé", "caze"]):
        return 2
    if any(token in source_text for token in ["fc", "club", "national", "team"]):
        return 3
    if any(token in source_text for token in ["onefootball", "reuters", "ap sports", "ap", "sports"]):
        return 4
    return 5


def group_articles(articles: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Group duplicate articles from different sources by normalized title."""
    groups: list[dict[str, Any]] = []
    for article in articles:
        title = normalize_text(article.get("title", ""))
        if not title:
            continue

        normalized_title = re.sub(r"\s+", " ", title).strip()
        match = next((group for group in groups if group["title_key"] == normalized_title), None)
        if match is None:
            groups.append(
                {
                    "title_key": normalized_title,
                    "title": article.get("title", "").strip(),
                    "sources": [article.get("source", "")],
                    "links": [article.get("link", "")],
                    "video_links": [article.get("video_url", "")] if article.get("video_url") else [],
                    "summary": article.get("summary", "") or article.get("description", ""),
                    "description": article.get("description", ""),
                    "official_source": article.get("official_source") or article.get("source", ""),
                    "match": article.get("match"),
                    "duplicate_keys": [article.get("duplicate_key") or article.get("video_id") or article.get("link") or article.get("title") or ""],
                }
            )
        else:
            if article.get("source", "") not in match["sources"]:
                match["sources"].append(article.get("source", ""))
            if article.get("link", "") and article.get("link", "") not in match["links"]:
                match["links"].append(article.get("link", ""))
            duplicate_key = article.get("duplicate_key") or article.get("video_id") or article.get("link") or article.get("title") or ""
            if duplicate_key and duplicate_key not in match.get("duplicate_keys", []):
                match.setdefault("duplicate_keys", []).append(duplicate_key)
            video_url = article.get("video_url", "")
            if video_url and video_url not in match.get("video_links", []):
                match.setdefault("video_links", []).append(video_url)
            official_source = article.get("official_source") or article.get("source", "")
            if official_source and (not match.get("official_source") or get_source_priority(official_source) < get_source_priority(str(match.get("official_source", "")))):
                match["official_source"] = official_source

    for group in groups:
        group["sources"] = sorted(group.get("sources", []), key=get_source_priority)
        group["video_url"] = next((url for url in group.get("video_links", []) if url), "")
        group["video_id"] = extract_youtube_video_id(group["video_url"])
        if not group.get("official_source"):
            group["official_source"] = group["sources"][0] if group.get("sources") else ""

    return groups


def is_youtube_link(value: str) -> bool:
    """Return True when the provided value points to YouTube."""
    normalized = (value or "").strip().lower()
    return "youtube.com" in normalized or "youtu.be" in normalized


def normalize_channel_name(value: Any) -> str:
    """Normalize a YouTube channel name for strict trusted-list comparison."""
    text = "".join(
        character for character in str(value or "")
        if not unicodedata.category(character).startswith(("P", "S"))
    )
    text = unicodedata.normalize("NFKD", text)
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = re.sub(r"[^a-zA-Z0-9\s]", "", text).casefold()
    return re.sub(r"\s+", " ", text).strip()


def channel_identity(value: Any) -> str:
    """Create a spacing-independent identity for trusted channel matching."""
    value_with_named_plus = str(value or "").replace("+", " plus ")
    return normalize_channel_name(value_with_named_plus).replace(" ", "")


def is_trusted_youtube_uploader(metadata: dict[str, Any]) -> bool:
    """Return True only for an exact trusted channel or uploader name."""
    names = {
        channel_identity(metadata.get("channel")),
        channel_identity(metadata.get("uploader")),
    }
    trusted_names = {channel_identity(name) for name in TRUSTED_YOUTUBE_CHANNELS}
    return bool(names & trusted_names)


def _contains_normalized_phrase(value: Any, phrase: str) -> bool:
    """Match a normalized word or phrase without substring false positives."""
    normalized = normalize_channel_name(value)
    normalized_phrase = normalize_channel_name(phrase)
    return bool(re.search(rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)", normalized))


def is_tvnz_sport_video(metadata: dict[str, Any]) -> bool:
    """Return True only for TVNZ Sport YouTube metadata."""
    uploader_names = {
        channel_identity(metadata.get("channel")),
        channel_identity(metadata.get("uploader")),
    }
    return channel_identity(YOUTUBE_DOWNLOAD_CHANNEL) in uploader_names


def is_tvnz_highlight_video(metadata: dict[str, Any]) -> bool:
    """Return whether a TVNZ Sport video title should be auto-downloaded."""
    if not is_tvnz_sport_video(metadata):
        return False
    title = metadata.get("title") or ""
    if any(_contains_normalized_phrase(title, term) for term in TVNZ_REJECTED_VIDEO_TERMS):
        return False
    return any(_contains_normalized_phrase(title, term) for term in TVNZ_HIGHLIGHT_KEYWORDS)


def validate_youtube_download_candidate(metadata: dict[str, Any]) -> tuple[bool, str]:
    """Allow downloads only for genuine TVNZ Sport match-highlight videos."""
    if not is_tvnz_sport_video(metadata):
        return False, "uploader is not TVNZ Sport"

    title = metadata.get("title") or ""
    for term in TVNZ_REJECTED_VIDEO_TERMS:
        if _contains_normalized_phrase(title, term):
            return False, f"title contains rejected term: {term}"
    if not any(_contains_normalized_phrase(title, term) for term in TVNZ_HIGHLIGHT_KEYWORDS):
        return False, "title is not a match highlight"
    return True, "TVNZ Sport match highlight"


def has_team_vs_team_pattern(title: Any) -> bool:
    """Return True when a title clearly names a match pairing."""
    return extract_match_teams(str(title or "")) is not None


def has_specific_event_context(title: Any, matched_match: dict[str, Any] | None = None) -> bool:
    """Return True when a single-event title includes a player/team/match context."""
    title_text = str(title or "")
    normalized = normalize_channel_name(title_text)
    if has_team_vs_team_pattern(title_text) or matched_match:
        return True
    if re.search(r"\b(?:against|versus|vs\.?|v\.?|x|for|in)\s+[a-z0-9]", normalized):
        return True
    context_names = (
        "messi", "ronaldo", "cristiano", "cr7", "neymar", "mbappe", "yamal",
        "argentina", "egypt", "france", "belgium", "brazil", "norway",
        "brasil", "switzerland", "colombia", "portugal", "spain",
    )
    return any(_contains_normalized_phrase(title_text, name) for name in context_names)


def _content_decision(category: str, should_alert: bool, should_download: bool, reason: str) -> dict[str, Any]:
    return {
        "category": category,
        "should_alert": should_alert,
        "should_download": should_download,
        "reason": reason,
    }


def classify_story_content(
    title: Any,
    source: Any = "",
    matched_match: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify a football story and decide Telegram/download eligibility."""
    title_text = str(title or "").strip()
    if not title_text:
        return _content_decision("UNKNOWN", False, False, "empty title")

    is_cazetv = channel_identity(source) == channel_identity("CazéTV")
    if any(_contains_normalized_phrase(title_text, term) for term in GENERIC_NEWS_TERMS):
        return _content_decision("GENERAL_NEWS", True, False, "title contains generic/listicle news term")

    non_download_checks: list[tuple[str, tuple[str, ...], str]] = [
        ("LIVE_STREAM", LIVE_STREAM_TERMS, "title contains live-stream term"),
        ("DISCUSSION", DISCUSSION_TERMS, "title contains discussion/opinion term"),
        ("TRANSFER_NEWS", TRANSFER_NEWS_TERMS, "title contains transfer-news term"),
    ]
    for category, terms, reason in non_download_checks:
        if any(_contains_normalized_phrase(title_text, term) for term in terms):
            should_alert = category in CONTENT_ALERT_CATEGORIES
            if is_cazetv and category in {"DISCUSSION", "LIVE_STREAM"}:
                should_alert = False
            return _content_decision(category, should_alert, False, reason)

    if any(_contains_normalized_phrase(title_text, term) for term in MATCH_HIGHLIGHT_TERMS):
        if has_specific_event_context(title_text, matched_match):
            return _content_decision("MATCH_HIGHLIGHT", True, True, "title contains match-highlight term with specific context")
        return _content_decision("GENERAL_NEWS", True, False, "highlight wording lacks a specific match or event context")

    if any(_contains_normalized_phrase(title_text, term) for term in SHOOTOUT_TERMS):
        should_download = has_specific_event_context(title_text, matched_match)
        return _content_decision("SHOOTOUT", True, should_download, "title contains shootout term with context" if should_download else "shootout wording lacks a specific event context")

    if any(_contains_normalized_phrase(title_text, term) for term in RED_CARD_TERMS):
        should_download = has_specific_event_context(title_text, matched_match)
        return _content_decision("RED_CARD", True, should_download, "title contains red-card term with context" if should_download else "red-card wording lacks a specific event context")

    if any(_contains_normalized_phrase(title_text, term) for term in VAR_OR_PENALTY_TERMS):
        should_download = has_specific_event_context(title_text, matched_match)
        return _content_decision("VAR_OR_PENALTY", True, should_download, "title contains VAR or penalty term with context" if should_download else "VAR or penalty wording lacks a specific event context")

    if any(_contains_normalized_phrase(title_text, term) for term in GOAL_CLIP_TERMS):
        should_download = has_specific_event_context(title_text, matched_match)
        return _content_decision("GOAL_CLIP", True, should_download, "title contains goal-clip term with context" if should_download else "goal wording lacks a specific event context")

    if has_team_vs_team_pattern(title_text) or matched_match:
        return _content_decision("GENERAL_NEWS", True, False, "match context found but no download event phrase matched")

    return _content_decision("GENERAL_NEWS", True, False, "no specific download category matched")


def is_download_eligible_title(title: Any) -> bool:
    """Return whether a story title warrants starting the YouTube download pipeline."""
    return bool(classify_story_content(title).get("should_download"))


def is_cazetv_discussion_content(source: Any, title: Any) -> bool:
    """Identify CazéTV discussion/opinion videos that should be ignored as news noise."""
    if channel_identity(source) != channel_identity("CazéTV"):
        return False
    return classify_story_content(title, source).get("category") in {"DISCUSSION", "LIVE_STREAM"}


def is_relevant_video_candidate(metadata: dict[str, Any], query: str) -> bool:
    """Reject obviously unrelated/unofficial search results."""
    title = normalize_channel_name(metadata.get("title"))
    if not title or any(term in title for term in UNOFFICIAL_VIDEO_TERMS):
        return False

    query_terms = {
        term for term in re.findall(r"[a-z0-9]+", normalize_channel_name(query))
        if len(term) >= 4 and term not in {"football", "video", "world", "highlights"}
    }
    return not query_terms or bool(query_terms & set(re.findall(r"[a-z0-9]+", title)))


def is_geo_restriction_error(value: Any) -> bool:
    """Detect the common yt-dlp messages for regional restrictions."""
    text = str(value or "").casefold()
    return any(term in text for term in GEO_RESTRICTION_TERMS)


HIGHLIGHT_TITLE_TERMS = ("highlights", "melhores momentos", "gols", "resumo")
HIGHLIGHT_REJECTED_TERMS = (
    "reaction", "live", "ao vivo", "jogo completo", "full match", "rap",
    "parody", "gaming", " ai ", "ai generated", "aigenerated", "ai video", "edit",
)
SPAM_UPLOADER_TERMS = ("spam", "reupload", "clips daily", "viral videos", "highlights hub")

TRUSTED_X_ACCOUNTS = {
    "FIFAcom", "FIFAWorldCup", "ESPNFC", "BBCSport", "SkyFootball",
    "CBSSportsGolazo", "UEFA", "premierleague", "TVNZSport", "NZ_Football",
}


def build_x_search_terms(article: dict[str, Any]) -> list[str]:
    """Build X discovery terms from the match, teams, competition, and relevant players."""
    title = str(article.get("title", "")).strip()
    terms = [title] if title else []
    teams = extract_match_teams(title)
    if teams:
        terms.extend(teams)
        terms.append(f"{teams[0]} vs {teams[1]}")
    competition = str(article.get("competition", "")).strip()
    if competition:
        terms.append(competition)
    normalized_title = normalize_text(title)
    terms.extend(keyword for keyword in KEYWORDS if len(keyword) >= 4 and keyword.lower() in normalized_title)
    return list(dict.fromkeys(term for term in terms if term))


def discover_x_posts(article: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Find relevant recent posts from trusted official X accounts."""
    bearer_token = str(config.get("x_bearer_token", "") or "")
    if not bearer_token:
        return []
    configured_accounts = {str(account).strip().lstrip("@") for account in config.get("x_official_accounts", []) if str(account).strip()}
    trusted_accounts = TRUSTED_X_ACCOUNTS | configured_accounts
    terms = build_x_search_terms(article)
    if not terms or not trusted_accounts:
        return []
    account_query = " OR ".join(f"from:{account}" for account in sorted(trusted_accounts)[:20])
    query = f'("{terms[0][:120]}") ({account_query}) -is:retweet'
    try:
        response = requests.get(
            "https://api.x.com/2/tweets/search/recent",
            headers={"Authorization": f"Bearer {bearer_token}"},
            params={
                "query": query,
                "max_results": 10,
                "tweet.fields": "created_at,public_metrics,author_id",
                "expansions": "author_id",
                "user.fields": "name,username,verified",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            logger.warning("X discovery request failed with status %s", response.status_code)
            return []
        payload = response.json()
    except (requests.RequestException, ValueError):
        logger.warning("X discovery request failed; continuing without X results.")
        return []

    users = {str(user.get("id")): user for user in payload.get("includes", {}).get("users", [])}
    trusted_normalized = {account.casefold() for account in trusted_accounts}
    relevant_terms = {term for term in re.findall(r"[a-z0-9]+", normalize_channel_name(" ".join(terms))) if len(term) >= 4}
    posts: list[dict[str, Any]] = []
    for post in payload.get("data", []):
        user = users.get(str(post.get("author_id")), {})
        username = str(user.get("username", ""))
        if username.casefold() not in trusted_normalized:
            continue
        text_value = str(post.get("text", ""))
        post_terms = set(re.findall(r"[a-z0-9]+", normalize_channel_name(text_value)))
        if relevant_terms and not (relevant_terms & post_terms):
            continue
        metrics = post.get("public_metrics", {}) or {}
        posts.append({
            "text": text_value,
            "account_name": user.get("name") or username,
            "username": username,
            "url": f"https://x.com/{username}/status/{post.get('id')}",
            "likes": metrics.get("like_count"),
            "reposts": metrics.get("retweet_count"),
            "views": metrics.get("impression_count"),
        })
    return posts


def build_x_telegram_message(post: dict[str, Any]) -> str:
    """Format an X discovery result for Telegram."""
    lines = [
        "X football discovery",
        f"Post: {post.get('text', '')}",
        f"Account: {post.get('account_name', '')}",
        f"URL: {post.get('url', '')}",
    ]
    for label, field in (("Likes", "likes"), ("Reposts", "reposts"), ("Views", "views")):
        if post.get(field) is not None:
            lines.append(f"{label}: {post[field]}")
    return "\n".join(lines)[:TELEGRAM_SAFE_TEXT_LIMIT]


def build_match_highlight_queries(team_one: str, team_two: str, competition: str) -> list[str]:
    """Build English and Portuguese searches for supported match competitions."""
    competition_key = normalize_channel_name(competition)
    if "brasileiro" in competition_key or "brasileirao" in competition_key:
        return [
            f"{team_one} vs {team_two} highlights Campeonato Brasileiro",
            f"{team_one} {team_two} melhores momentos Brasileirão",
        ]
    if "champions" in competition_key:
        return [
            f"{team_one} vs {team_two} highlights Champions League",
            f"{team_one} vs {team_two} melhores momentos Champions League",
        ]
    return [
        f"{team_one} vs {team_two} highlights World Cup 2026",
        f"{team_one} {team_two} melhores momentos Copa 2026",
    ]


TEAM_NAMES_PT = {"egypt": "Egito", "brazil": "Brasil", "spain": "Espanha", "switzerland": "Suíça"}

SUPPORTED_FIXTURE_COMPETITIONS = {
    "fifa world cup", "champions league", "campeonato brasileiro", "premier league",
    "la liga", "serie a", "bundesliga", "ligue 1",
}


def normalize_fixture_competition(name: Any, country: Any = "") -> str:
    """Map provider league names to the supported competition names."""
    league = normalize_channel_name(name)
    league_country = normalize_channel_name(country)
    if "world cup" in league:
        return "FIFA World Cup"
    if "champions league" in league:
        return "Champions League"
    if "brasileir" in league or (league == "serie a" and league_country == "brazil"):
        return "Campeonato Brasileiro"
    if "premier league" in league:
        return "Premier League"
    if league in {"la liga", "primera division"} and league_country == "spain":
        return "La Liga"
    if league == "serie a" and league_country == "italy":
        return "Serie A"
    if "bundesliga" in league and league_country == "germany":
        return "Bundesliga"
    if league == "ligue 1" and league_country == "france":
        return "Ligue 1"
    return ""


def load_todays_fixtures(config: dict[str, Any]) -> list[dict[str, str]]:
    """Load today's supported fixtures, preferring an explicit config list."""
    configured = config.get("today_matches", [])
    if isinstance(configured, list) and configured:
        return configured
    api_key = str(config.get("football_api_key", "") or "")
    if not api_key:
        return []
    timezone_name = str(config.get("football_api_timezone", "Pacific/Auckland"))
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        local_zone = timezone.utc
    fixture_date = datetime.now(local_zone).date().isoformat()
    try:
        response = requests.get(
            str(config.get("football_api_url", "https://v3.football.api-sports.io/fixtures")),
            headers={"x-apisports-key": api_key},
            params={"date": fixture_date, "timezone": timezone_name},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        logger.warning("Fixture loading failed; monitoring will continue without fixtures.")
        return []

    fixtures: list[dict[str, str]] = []
    for item in payload.get("response", []):
        league = item.get("league", {}) or {}
        competition = normalize_fixture_competition(league.get("name"), league.get("country"))
        if not competition:
            continue
        fixture = item.get("fixture", {}) or {}
        teams = item.get("teams", {}) or {}
        home = str((teams.get("home", {}) or {}).get("name", "")).strip()
        away = str((teams.get("away", {}) or {}).get("name", "")).strip()
        if not home or not away:
            continue
        fixture_datetime = str(fixture.get("date", ""))
        kickoff_time = fixture_datetime[11:16] if len(fixture_datetime) >= 16 else fixture_datetime
        status_data = fixture.get("status", {}) or {}
        fixtures.append({
            "home_team": home,
            "away_team": away,
            "competition": competition,
            "kickoff_time": kickoff_time,
            "status": str(status_data.get("short") or status_data.get("long") or ""),
        })
    return fixtures


def build_match_day_queries(match: dict[str, Any]) -> list[str]:
    """Build team-first YouTube searches for a configured match."""
    home = str(match.get("home_team", "")).strip()
    away = str(match.get("away_team", "")).strip()
    competition = str(match.get("competition", "")).strip()
    if not home or not away:
        return []
    return [
        f"{home} vs {away} highlights {competition}".strip(),
        f"{home} {away} match highlights",
        f"{home} {away} melhores momentos",
        f"{home} x {away} melhores momentos",
    ]


def attach_article_to_match(
    article: dict[str, Any], matches: list[dict[str, Any]]
) -> dict[str, Any]:
    """Attach the first configured fixture whose home or away team appears in the story."""
    title = normalize_channel_name(article.get("title"))
    supporting_text = normalize_channel_name(
        " ".join(str(article.get(field, "")) for field in ("summary", "description"))
    )
    best_match: dict[str, Any] | None = None
    best_score = 0
    for match in matches:
        home = normalize_channel_name(match.get("home_team"))
        away = normalize_channel_name(match.get("away_team"))
        score = 0
        score += 3 if home and home in title else 0
        score += 3 if away and away in title else 0
        score += 1 if home and home in supporting_text else 0
        score += 1 if away and away in supporting_text else 0
        if score > best_score:
            best_score = score
            best_match = match
    if best_match is not None:
        article["match"] = {
            field: str(best_match.get(field, "")).strip()
            for field in ("home_team", "away_team", "competition", "kickoff_time", "status")
        }
        logger.info(
            "Matched today's game: %s vs %s",
            article["match"]["home_team"], article["match"]["away_team"],
        )
    return article


def extract_match_teams(value: str) -> tuple[str, str] | None:
    """Extract two team names from common match-title separators."""
    match = re.search(
        r"^\s*(.+?)\s+(?:vs\.?|v\.?|x)\s+(.+?)(?:\s*[-:|]|\s+(?:highlights|melhores momentos|gols|resumo)\b|$)",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def validate_highlight_candidate(
    metadata: dict[str, Any],
    team_names: tuple[str, str] | None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Validate a recent match-highlights upload, including non-official channels."""
    title = normalize_channel_name(metadata.get("title"))
    padded_title = f" {title} "
    if any(term in padded_title for term in HIGHLIGHT_REJECTED_TERMS):
        return False, "title contains rejected content"
    if not any(term in title for term in HIGHLIGHT_TITLE_TERMS):
        return False, "title is not a highlights video"
    has_both_teams = bool(team_names) and all(normalize_channel_name(team) in title for team in team_names)
    if not has_both_teams:
        return False, "title does not identify both teams"

    try:
        duration = float(metadata.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if not 120 <= duration <= 900:
        return False, "duration is outside 2-15 minutes"

    uploader = normalize_channel_name(metadata.get("channel") or metadata.get("uploader"))
    if not uploader or any(term in uploader for term in SPAM_UPLOADER_TERMS):
        return False, "uploader appears to be spam"

    uploaded_at: datetime | None = None
    upload_date = str(metadata.get("upload_date") or "")
    if re.fullmatch(r"\d{8}", upload_date):
        uploaded_at = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc)
    elif metadata.get("timestamp"):
        try:
            uploaded_at = datetime.fromtimestamp(float(metadata["timestamp"]), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            uploaded_at = None
    if uploaded_at is None:
        return False, "upload date is missing"
    reference_time = now or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    age_days = (reference_time - uploaded_at).total_seconds() / 86400
    if age_days < -1 or age_days > 14:
        return False, "video is not recent"
    return True, "valid recent match highlights"


def rank_highlight_candidates(
    candidates: list[dict[str, Any]], team_names: tuple[str, str] | None
) -> list[dict[str, Any]]:
    """Log and return acceptable match-highlight candidates, newest first."""
    accepted: list[dict[str, Any]] = []
    for candidate in candidates:
        valid_download, download_reason = validate_youtube_download_candidate(candidate)
        if not valid_download:
            logger.info("Highlight candidate rejected because %s: %s", download_reason, candidate.get("title", ""))
            continue
        valid, reason = validate_highlight_candidate(candidate, team_names)
        title = candidate.get("title", "")
        if valid:
            logger.info("Highlight candidate accepted: %s", title)
            accepted.append(candidate)
        else:
            logger.info("Highlight candidate rejected because %s: %s", reason, title)
    return sorted(
        accepted,
        key=lambda candidate: str(candidate.get("upload_date") or candidate.get("timestamp") or ""),
        reverse=True,
    )


def rank_official_youtube_candidates(
    candidates: list[dict[str, Any]],
    query: str,
    seen_video_ids: set[str],
    channel_priority: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return all acceptable candidates in official-channel priority order."""
    query_terms = set(re.findall(r"[a-z0-9]+", normalize_channel_name(query)))
    priority_names = channel_priority or TRUSTED_YOUTUBE_CHANNEL_ORDER
    channel_order = {
        channel_identity(name): index for index, name in enumerate(priority_names)
    }
    ranked_candidates: list[tuple[int, int, dict[str, Any]]] = []
    for candidate in candidates:
        uploader = candidate.get("channel") or candidate.get("uploader") or "unknown"
        video_id = str(candidate.get("id") or "")
        candidate_url = candidate.get("webpage_url") or candidate.get("url")
        if not candidate_url and video_id:
            candidate_url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info(
            "YouTube candidate | title=%s | uploader=%s | url=%s",
            candidate.get("title", ""), uploader, candidate_url or "unknown",
        )
        valid_download, download_reason = validate_youtube_download_candidate(candidate)
        if not valid_download:
            logger.info("Skipping YouTube download because %s: %s", download_reason, uploader)
            continue
        if not video_id or video_id in seen_video_ids:
            continue
        if not is_relevant_video_candidate(candidate, query):
            logger.info("Skipping official-channel video because it is not relevant to the article: %s", candidate.get("title", ""))
            continue
        title_terms = set(re.findall(r"[a-z0-9]+", normalize_channel_name(candidate.get("title"))))
        channel_name = candidate.get("channel") or candidate.get("uploader") or ""
        priority = channel_order.get(channel_identity(channel_name), len(channel_order))
        ranked_candidates.append((priority, -len(query_terms & title_terms), candidate))

    ranked_candidates.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ranked_candidates]


def select_official_youtube_candidate(
    candidates: list[dict[str, Any]], query: str, seen_video_ids: set[str]
) -> dict[str, Any] | None:
    """Select the highest-priority relevant candidate."""
    ranked_candidates = rank_official_youtube_candidates(candidates, query, seen_video_ids)

    if not ranked_candidates:
        return None

    selected = ranked_candidates[0]
    uploader = selected.get("channel") or selected.get("uploader") or "unknown"
    logger.info("Official channel found: %s", uploader)
    return selected


def extract_youtube_video_id(value: str) -> str:
    """Extract a YouTube video ID from a URL or short-link string when available."""
    if not value:
        return ""
    normalized = (value or "").strip()
    match = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", normalized)
    if match:
        return match.group(1)
    match = re.search(r"youtu\.be/([A-Za-z0-9_-]{11})", normalized)
    if match:
        return match.group(1)
    match = re.search(r"youtube\.com/shorts/([A-Za-z0-9_-]{11})", normalized)
    if match:
        return match.group(1)
    return ""


def get_debug_acceptance_reason(title: str, sources: list[str] | None = None, viral_score: Any = None) -> str:
    """Describe why a story should be accepted or rejected for Telegram alerts."""
    title_text = normalize_text(title)
    source_names = [normalize_text(source) for source in (sources or []) if str(source)]
    high_trigger_terms = ["goal", "gol", "golaço", "argentina", "cabo verde", "messi", "var", "penalty", "red card", "fight", "controversy", "reaction"]

    for term in high_trigger_terms:
        if term in title_text:
            return f"accepted because title contains trigger term '{term}'"

    if any(source_name == "cazétv" or source_name == "caze" for source_name in source_names):
        matched_keywords = [keyword for keyword in KEYWORDS if keyword.lower() in title_text]
        if matched_keywords:
            return f"accepted because CazéTV title matched keywords: {', '.join(matched_keywords)}"

    if title_text and "messi" in title_text and any(term in title_text for term in ["goal", "free kick", "penalty"]):
        return "accepted because Messi title contains a major goal-style trigger"

    if viral_score is not None:
        try:
            if float(viral_score) >= 75:
                return "accepted because viral score met the alert threshold"
        except (TypeError, ValueError):
            pass

    return "rejected because no high-impact trigger terms were found"


def should_send_notification(grouped_article: dict[str, Any]) -> bool:
    """Notify for high-score stories, Messi-specific moments, or CazéTV keyword matches."""
    title = normalize_text(grouped_article.get("title", ""))
    sources = [str(source) for source in grouped_article.get("sources", []) if str(source)]

    if any(term in title for term in ["goal", "gol", "golaço", "argentina", "cabo verde", "messi", "var", "penalty", "red card", "fight", "controversy", "reaction"]):
        return True

    if any(source == "CazéTV" for source in sources) and any(keyword.lower() in title for keyword in KEYWORDS):
        return True

    if title and "messi" in title and any(term in title for term in ["goal", "free kick", "penalty"]):
        return True

    viral_score = grouped_article.get("viral_score")
    if viral_score is not None:
        return float(viral_score) >= 75

    score = float(grouped_article.get("score", 0) or 0)
    if score > 10:
        return score >= 75
    return score >= VIRAL_SCORE_THRESHOLD


def load_alerts(alerts_file: Path) -> list[dict[str, Any]]:
    """Load previously stored alerts from disk."""
    if not alerts_file.exists():
        return []
    try:
        data = json.loads(alerts_file.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read alerts file %s: %s", alerts_file, exc)
    return []


def save_alerts(alerts_file: Path, alerts: list[dict[str, Any]]) -> None:
    """Persist alerts to disk."""
    alerts_file.write_text(json.dumps(alerts, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize_media_id(url: Any) -> str:
    """Return a stable cross-run identity for supported video/social URLs."""
    value = str(url or "").strip()
    if not value:
        return ""
    patterns = (
        ("youtube", r"(?:youtube\.com/watch\?(?:[^#\s]*&)?v=|youtu\.be/)([A-Za-z0-9_-]+)"),
        ("youtube", r"youtube\.com/shorts/([A-Za-z0-9_-]+)"),
        ("instagram", r"instagram\.com/(?:reel|p|tv)/([^/?#]+)"),
        ("tiktok", r"tiktok\.com/(?:@[^/]+/)?video/(\d+)"),
        ("x", r"(?:x|twitter)\.com/[^/]+/status/(\d+)"),
    )
    for platform, pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if match:
            return f"{platform}:{match.group(1)}"
    normalized = value.casefold().split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return f"url:{normalized}"


def _state_dir(config: dict[str, Any]) -> Path | None:
    configured = config.get("monitor_state_dir")
    return Path(configured) if configured else None


def load_persistent_state(config: dict[str, Any], force_reload: bool = False) -> dict[str, dict[str, Any]] | None:
    state_dir = _state_dir(config)
    if state_dir is None:
        return None
    cache_key = str(state_dir.resolve())
    if cache_key in PERSISTENT_STATE_CACHE and not force_reload:
        return PERSISTENT_STATE_CACHE[cache_key]
    state: dict[str, dict[str, Any]] = {}
    for category, filename in PERSISTENT_STATE_FILES.items():
        path = state_dir / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                payload = {str(item): {"timestamp": ""} for item in payload}
            state[category] = payload if isinstance(payload, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            state[category] = {}
    legacy_telegram_keys = [
        key for key in state.get("sent_video_ids", {})
        if str(key).startswith(("telegram:", "telegram_short:"))
    ]
    if legacy_telegram_keys:
        for key in legacy_telegram_keys:
            state["sent_video_ids"].pop(key, None)
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / PERSISTENT_STATE_FILES["sent_video_ids"]).write_text(
                json.dumps(state["sent_video_ids"], ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info("Removed %s legacy Telegram entries from sent_video_ids.json", len(legacy_telegram_keys))
        except OSError:
            logger.warning("Could not clean legacy Telegram entries from sent_video_ids.json")
    PERSISTENT_STATE_CACHE[cache_key] = state
    logger.info("Persistent state loaded: %s", state_dir)
    return state


def save_persistent_state(config: dict[str, Any]) -> None:
    state_dir = _state_dir(config)
    state = load_persistent_state(config)
    if state_dir is None or state is None:
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    for category, filename in PERSISTENT_STATE_FILES.items():
        (state_dir / filename).write_text(
            json.dumps(state.get(category, {}), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    logger.info("Persistent state saved: %s", state_dir)


def reset_persistent_state_runtime() -> None:
    """Forget in-memory dedupe data while leaving persisted files intact."""
    PERSISTENT_STATE_CACHE.clear()
    RUN_DEDUPE_KEYS.clear()
    RESERVED_SEND_IDS.clear()


def persistent_state_contains(
    config: dict[str, Any], category: str, media_id: str, ttl_hours: float | None = None,
) -> bool:
    state = load_persistent_state(config)
    if state is None or not media_id:
        return False
    record = state.get(category, {}).get(media_id)
    if record is None:
        return False
    if ttl_hours is None:
        return True
    try:
        timestamp = datetime.fromisoformat(str(record.get("timestamp") or ""))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - timestamp).total_seconds() < ttl_hours * 3600
    except (ValueError, TypeError, AttributeError):
        return False


def mark_persistent_state(
    config: dict[str, Any], category: str, media_id: str, source: str = "", title: str = "", url: str = "",
) -> None:
    state = load_persistent_state(config)
    if state is None or not media_id:
        return
    state.setdefault(category, {})[media_id] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source, "title": title, "url": url,
    }
    RUN_DEDUPE_KEYS.add(f"{category}:{media_id}")
    save_persistent_state(config)
    if category == "downloaded_video_ids":
        label = "downloaded"
    elif category.startswith("skipped_"):
        label = "blocked"
    else:
        label = "sent"
    logger.info("Marked as %s: %s", label, media_id)


def log_duplicate(source: Any, title: Any, url: Any, media_id: str) -> None:
    logger.info("Duplicate skipped: %s | %s | %s | %s", source, title, url, media_id)


def should_send_once(
    item_id: str,
    url: str,
    title: str,
    source: str,
    kind: str,
    config: dict[str, Any],
) -> bool:
    """Atomically reserve an unsent media ID for one Telegram attempt in this run."""
    state_dir = _state_dir(config)
    if state_dir is None or not item_id:
        return True
    reservation_key = f"{state_dir.resolve()}|{item_id}"
    manual_ttl = float(config.get("manual_link_duplicate_ttl_hours", 48) or 48)
    duplicate = (
        reservation_key in RESERVED_SEND_IDS
        or persistent_state_contains(config, "sent_alerts", item_id)
        or persistent_state_contains(config, "sent_video_ids", item_id)
        or persistent_state_contains(
            config, "manual_open_links", item_id,
            manual_ttl if kind == "manual_open" else None,
        )
    )
    if duplicate:
        logger.info("Duplicate skipped: %s | %s | %s | %s | %s", kind, source, title, url, item_id)
        return False
    RESERVED_SEND_IDS.add(reservation_key)
    logger.info("Reserved in memory: %s", item_id)
    return True


def release_send_reservation(config: dict[str, Any], item_id: str) -> None:
    state_dir = _state_dir(config)
    if state_dir is not None and item_id:
        RESERVED_SEND_IDS.discard(f"{state_dir.resolve()}|{item_id}")


def mark_as_sent(
    config: dict[str, Any], category: str, item_id: str,
    source: str, title: str, url: str,
) -> None:
    """Commit one successful Telegram delivery to exactly one persistent category."""
    mark_persistent_state(config, category, item_id, source, title, url)


def build_video_search_url(query: str) -> str:
    """Create a YouTube search link for a video if no official video is found."""
    return f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}"


def find_official_video_url(grouped_article: dict[str, Any]) -> str:
    """Return an official video URL when obvious sources are present, otherwise a YouTube search link."""
    title = grouped_article.get("title", "")
    sources = " ".join(grouped_article.get("sources", []))
    query = f"{title} {sources} official video"
    query = re.sub(r"\s+", " ", query).strip()
    return build_video_search_url(query)


def build_manual_grouped_article(headline: str) -> dict[str, Any]:
    """Create a grouped article payload for a manually-triggered breaking-news alert."""
    grouped_article: dict[str, Any] = {
        "title": headline.strip(),
        "summary": f"Manual breaking-news alert: {headline.strip()}",
        "description": f"Manual breaking-news alert: {headline.strip()}",
        "sources": ["Manual"],
        "links": [],
        "score": 10.0,
        "reason": "Manual breaking-news trigger",
        "is_manual_event": True,
        "match": headline.strip(),
        "official_source": "Manual",
    }
    shorts_pack = build_portuguese_shorts_pack(grouped_article, {})
    grouped_article.update(shorts_pack)
    return grouped_article


def controversy_score_for_title(title: Any) -> int:
    """Score controversial football wording from 0-100."""
    normalized = normalize_channel_name(title)
    matched = [
        term for term in CONTROVERSY_KEYWORDS_PT + CONTROVERSY_KEYWORDS_EN
        if _contains_normalized_phrase(normalized, term)
    ]
    if not matched:
        return 0
    score = 45 + min(30, (len(set(matched)) - 1) * 10)
    if any(_contains_normalized_phrase(normalized, term) for term in HIGH_CONTROVERSY_TERMS):
        score += 30
    return min(100, score)


def is_brazilian_sports_source(source: Any) -> bool:
    identity = channel_identity(source)
    return any(identity == channel_identity(name) for name in BRAZILIAN_SPORTS_SOURCES)


def source_category(source: Any, url: str = "") -> str:
    platform = video_platform_from_url(url)
    if platform in {"X/Twitter", "TikTok", "Instagram"}:
        return "social_manual_source"
    if is_brazilian_sports_source(source):
        return "brazilian_sports_source"
    if channel_identity(source) in {channel_identity("TVNZ Sport"), channel_identity("FIFA")}:
        return "official_video_source"
    return "news_source"


def apply_priority_scores(article: dict[str, Any]) -> dict[str, Any]:
    """Attach controversy, viral, source, and final priority scores."""
    controversy_score = controversy_score_for_title(article.get("title", ""))
    viral_score = calculate_viral_score(article) if "viral_score" not in article else int(float(article.get("viral_score") or 0))
    sources = article.get("sources") or [article.get("source", "")]
    source_score = max(
        (25 if is_brazilian_sports_source(source) else 20 if channel_identity(source) in {
            channel_identity("TVNZ Sport"), channel_identity("FIFA")
        } else 10 for source in sources if source),
        default=0,
    )
    article["controversy_score"] = controversy_score
    article["viral_score"] = viral_score
    article["source_score"] = source_score
    article["final_priority_score"] = min(100, round(controversy_score * 0.55 + viral_score * 0.3 + source_score * 0.15))
    return article


def calculate_viral_score(grouped_article: dict[str, Any]) -> int:
    """Calculate a 0-100 viral potential score for a football story."""
    title = normalize_text(grouped_article.get("title", ""))
    summary = normalize_text(grouped_article.get("summary", "") or grouped_article.get("description", ""))
    sources = [str(source) for source in grouped_article.get("sources", []) if str(source)]
    video_links = [str(link) for link in grouped_article.get("video_links", []) if str(link)]
    if grouped_article.get("video_url") and str(grouped_article.get("video_url")) not in video_links:
        video_links.append(str(grouped_article.get("video_url")))
    grouped_article["video_links"] = video_links # Ensure video_links is always present
    article_links = [str(link) for link in grouped_article.get("links", []) if str(link)]

    score = 0
    if any(keyword in title for keyword in ["goal", "gol", "golaço", "penalty", "free kick", "red card", "var", "controversy", "injury", "transfer", "fan reaction", "fight", "dramatic"]):
        score += 24
    if any(star in title for star in ["messi", "ronaldo", "neymar", "vinicius", "mbappe", "haaland", "bellingham", "yamal"]):
        score += 24
    if any(marker in summary for marker in ["viral", "explod", "breaking", "official", "trending", "dramatic", "polêmica", "reação", "torcida"]):
        score += 10
    if any(source.lower().startswith("fifa") for source in sources):
        score += 16
    if len(sources) >= 2:
        score += min(16, (len(sources) - 1) * 4)
    if len(article_links) >= 2:
        score += 6
    if video_links:
        score += min(16, len(video_links) * 8)
    if any(source.lower().startswith("bbc") or source.lower().startswith("sky") or source.lower().startswith("espn") or source.lower().startswith("fox") or source.lower().startswith("tnt") for source in sources):
        score += 0

    return max(0, min(100, round(score)))


def sanitize_telegram_message(message: str) -> str:
    """Sanitize a message for Telegram by removing problematic markdown/HTML and truncating if too long."""
    # Remove common markdown/HTML characters that cause issues
    sanitized_message = re.sub(r"[*_`[\]()~>#+=|{}.!-<>]", "", message) # Remove special characters
    sanitized_message = re.sub(r"<[^>]+>", "", sanitized_message)  # Remove HTML tags

    # Truncate if over 3500 characters
    if len(sanitized_message) > 3500:
        sanitized_message = sanitized_message[:3497] + "..."
    return sanitized_message


TELEGRAM_SAFE_TEXT_LIMIT = 3499
TELEGRAM_VIDEO_FILE_LIMIT_BYTES = 45 * 1024 * 1024
PUBLIC_TELEGRAM_FALLBACK_TEXT = "Esse assunto está movimentando o futebol e pode render um bom comentário para Shorts."
INTERNAL_TELEGRAM_TEXT_TERMS = (
    "no ai api key configured",
    "falling back to heuristics",
    "ai scoring unavailable",
    "fallback used",
)


def public_telegram_text(value: Any, fallback: str = PUBLIC_TELEGRAM_FALLBACK_TEXT) -> str:
    """Return Telegram-safe public wording, hiding internal config/debug details."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    normalized = normalize_channel_name(text)
    if not text or any(term in normalized for term in INTERNAL_TELEGRAM_TEXT_TERMS):
        return fallback
    return text


def prepare_telegram_message(grouped_article: dict[str, Any], message: str) -> str:
    """Keep Telegram payloads below the safe limit while preserving core fields."""
    if len(message) <= TELEGRAM_SAFE_TEXT_LIMIT:
        return message

    sources = grouped_article.get("sources", [])
    source_text = ", ".join(str(source) for source in sources if source) or str(grouped_article.get("source", ""))
    links = grouped_article.get("links", [])
    original_url = str(grouped_article.get("link", "") or (links[0] if links else ""))
    video_links = grouped_article.get("video_links", [])
    video_url = str(grouped_article.get("video_url", "") or (video_links[0] if video_links else ""))
    caption = str(
        grouped_article.get("shorts_title")
        or grouped_article.get("description")
        or grouped_article.get("summary")
        or grouped_article.get("title", "")
    ).strip()
    caption = re.sub(r"\s+", " ", caption)[:500]
    score = grouped_article.get("viral_score", grouped_article.get("score", 0))

    compact_fields = [
        f"Título: {str(grouped_article.get('title', '')).strip()[:500]}",
        f"Score: {score}",
        f"Fonte: {source_text[:300]}",
        f"URL original: {original_url[:1000]}",
    ]
    if video_url:
        compact_fields.append(f"URL do vídeo: {video_url[:1000]}")
    compact_fields.append(f"Ideia de legenda curta: {caption}")
    compact_fields.append("Mensagem resumida automaticamente para respeitar o limite do Telegram.")
    return "\n".join(compact_fields)[:TELEGRAM_SAFE_TEXT_LIMIT]


def send_x_discovery_notification(post: dict[str, Any], config: dict[str, Any]) -> bool:
    """Send one X discovery link to Telegram without entering the download pipeline."""
    token = str(config.get("telegram_bot_token", "") or "")
    chat_id = str(config.get("telegram_chat_id", "") or "")
    if not token or not chat_id:
        return False
    message = build_x_telegram_message(post).replace(token, "[REDACTED]")
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            logger.warning("X discovery Telegram notification failed with status %s", response.status_code)
            return False
        return True
    except requests.RequestException:
        logger.warning("X discovery Telegram notification failed; monitoring will continue.")
        return False


def build_portuguese_shorts_pack(grouped_article: dict[str, Any], config: dict[str, str]) -> dict[str, Any]:
    """Create a complete Portuguese-BR Shorts package for a high-impact football article."""
    original_title = grouped_article.get("title", "")
    original_source = ", ".join(grouped_article.get("sources", []))
    summary = grouped_article.get("summary", "") or grouped_article.get("description", "")
    score = grouped_article.get("score", 0)
    reason = grouped_article.get("reason", "")
    viral_score = calculate_viral_score(grouped_article)

    title = original_title
    if len(title) > 60:
        title = title[:57].rstrip() + "..."

    title_pt = f"{title} virou febre?"
    if len(title_pt) > 60:
        title_pt = title_pt[:57].rstrip() + "..."

    thumbnail_text_options = [
        "Polêmica no futebol",
        "Cena de cinema",
        "Futebol em choque",
    ]
    frame_idea = "Close no jogador e reação da torcida"
    presenter_expression = "Surpresa + empolgação"
    background_idea = "Estádio lotado com câmera rápida e efeito de destaque"

    scripts = {
        "30s": (
            f"Ô meu Deus, isso aqui tá pegando fogo! {original_title} virou assunto em tudo quanto é canto. "
            "A cena foi tão absurda que a galera quase caiu da cadeira. E agora, você acha que isso vai virar meme ou vai virar lenda?"
        ),
        "45s": (
            f"Peraí, isso aqui não é notícia comum não. {original_title} virou o tema da semana no futebol. "
            "Tem drama, tem reação, tem polêmica e ainda tem aquele clima de filme. "
            "Se você gosta de futebol e de confusão boa, esse assunto não sai da cabeça de ninguém. E aí, qual foi a parte mais absurda pra você?"
        ),
        "60s": (
            f"Olha só, a bola não parou de rolar nem na cabeça da galera. {original_title} é o tipo de assunto que faz todo mundo comentar. "
            "Tem gol, tem emoção, tem chance de virar meme, e ainda tem aquele toque de drama que faz o Shorts explodir. "
            "É o futebol sendo futebol, só que mais exagerado, mais engraçado e mais impossível de ignorar. Então, você acha que isso vai virar clássico ou vai virar piada da semana?"
        ),
    }

    heygen_narration = (
        f"Isso aqui tá pegando fogo. {original_title} virou assunto em todo lugar. "
        "A reação foi enorme, o drama foi real, e a galera não parou de comentar. "
        "Se isso não é motivo pra virar Shorts, eu não sei o que é."
    )

    description = (
        f"Essa notícia de futebol tá fazendo a galera falar!\n\n"
        f"Título original: {original_title}\n"
        f"Fonte: {original_source}\n\n"
        "Se você curte futebol, polêmica, drama, gol, VAR e aquele momento que explode na timeline, esse Shorts é pra você.\n"
        "Comenta sua opinião, porque esse tipo de situação sempre gera discussão.\n\n"
        "#Futebol #FutebolBrasil #ShortsFutebol #Gol #Var #Polêmica #Messi #CristianoRonaldo #Neymar #ViniJr"
    )
    hashtags = ["#Futebol", "#FutebolBrasil", "#ShortsFutebol", "#Gol", "#Var", "#Polêmica", "#Messi", "#CristianoRonaldo", "#Neymar", "#ViniJr"]
    keywords = [
        "futebol shorts",
        "momento viral futebol",
        "polêmica futebol",
        "drama futebol",
        "resumo futebol",
        "futebol brasil",
    ]

    video_search_links: list[str] = []

    # Initialize video_links if it doesn't exist
    if "video_links" not in grouped_article or not isinstance(grouped_article["video_links"], list):
        grouped_article["video_links"] = []

    existing_video_url = grouped_article.get("video_url") or grouped_article.get("link") or ""
    if existing_video_url and ("youtube.com" in existing_video_url or "youtu.be" in existing_video_url):
        if existing_video_url not in grouped_article["video_links"]:
            grouped_article["video_links"].append(existing_video_url)
        grouped_article["video_url"] = existing_video_url
    else:
        grouped_article["video_url"] = ""

    grouped_article["shorts_title"] = title_pt
    grouped_article["thumbnail_text"] = thumbnail_text_options
    grouped_article["thumbnail_frame_idea"] = frame_idea
    grouped_article["thumbnail_expression"] = presenter_expression
    grouped_article["thumbnail_background"] = background_idea
    grouped_article["narration_scripts"] = scripts
    grouped_article["heygen_narration"] = heygen_narration
    grouped_article["description"] = description
    grouped_article["hashtags"] = hashtags
    grouped_article["viral_reason"] = reason or f"Tema quente com cara de explosão em Shorts: {summary[:120]}"
    grouped_article["search_keywords"] = keywords
    grouped_article["source"] = original_source
    grouped_article["score"] = float(score or 0)
    grouped_article["viral_score"] = viral_score
    grouped_article["video_search_links"] = video_search_links
    grouped_article["video_links"] = grouped_article.get("video_links", [])
    grouped_article["suggested_hook"] = f"{original_title} está dominando o futebol e todo mundo está falando"
    grouped_article["suggested_cta"] = "Comenta o que você achou e segue pra mais conteúdo de futebol"
    grouped_article["seo_description"] = (
        f"Resumo rápido de {original_title} com contexto, reação e o que faz essa história ser tão comentada no futebol."
    )
    return grouped_article


def score_article_with_ai(grouped_article: dict[str, Any], config: dict[str, str]) -> dict[str, Any]:
    """Use OpenAI-compatible chat completions when an API key is configured, else fall back to heuristics."""
    title = grouped_article.get("title", "")
    summary = grouped_article.get("summary", "") or grouped_article.get("description", "")
    prompt = (
        "You are scoring football news articles for YouTube Shorts viral potential from 0 to 10. "
        f"Title: {title}\nSummary: {summary}\n"
        "Return JSON with fields 'score' and 'reason'. Score should be an integer 0-10. "
        "Reason should explain why it may go viral."
    )

    api_key = config.get("openai_api_key", "")
    if not api_key:
        score = 0
        reason = "No AI API key configured; falling back to heuristics."
        if any(keyword.lower() in normalize_text(title) for keyword in KEYWORDS):
            score = 8 if any(token in normalize_text(title) for token in ["red card", "penalty", "controversy", "dramatic reaction", "fan reaction", "funny moment", "referee mistake", "fight", "free kick", "goal"]) else 7
        grouped_article["score"] = float(score)
        grouped_article["reason"] = reason
        return grouped_article

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": config.get("openai_model", "gpt-4o-mini"),
                "messages": [{"role": "system", "content": "You are a football content analyst."}, {"role": "user", "content": prompt}],
                "temperature": 0.2,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        message = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        parsed = json.loads(message)
        grouped_article["score"] = float(parsed.get("score", 0))
        grouped_article["reason"] = str(parsed.get("reason", ""))
        grouped_article["viral_score"] = calculate_viral_score(grouped_article)
        return grouped_article
    except Exception as exc:
        logger.warning("AI scoring failed, falling back to heuristic: %s", exc)
        grouped_article["score"] = 0.0
        grouped_article["reason"] = "AI scoring unavailable; fallback used."
        grouped_article["viral_score"] = calculate_viral_score(grouped_article)
        return grouped_article


def build_article_key(article: dict[str, str], source: str) -> str:
    """Build a stable identifier so duplicates can be filtered consistently."""
    video_key = article.get("video_url") or article.get("video_id")
    link = video_key or article.get("link") or article.get("id") or article.get("title") or ""
    if not link:
        raise ValueError("Article is missing a usable identifier")
    return f"{source}:{link}"


def load_seen_articles(state_file: Path) -> set[str]:
    """Load previously seen article identifiers from disk."""
    if not state_file.exists():
        return set()

    try:
        raw_data = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read state file %s: %s", state_file, exc)
        return set()

    if isinstance(raw_data, list):
        return {str(item) for item in raw_data}
    if isinstance(raw_data, dict):
        return {str(item) for item in raw_data.get("seen_articles", [])}
    return set()


def save_seen_articles(state_file: Path, article_ids: set[str]) -> None:
    """Persist the seen article identifiers for future runs."""
    state_file.write_text(json.dumps(sorted(article_ids), indent=2), encoding="utf-8")


def get_entry_value(entry: Any, field: str, default: str = "") -> str:
    """Safely read values from either a dict-like or object-like feed entry."""
    if isinstance(entry, dict):
        return str(entry.get(field, default) or default)
    return str(getattr(entry, field, default) or default)


def normalize_entry(entry: Any, source: str) -> dict[str, str]:
    """Convert a feedparser entry into a normalized article dictionary."""
    link = get_entry_value(entry, "link", "")
    video_url = get_entry_value(entry, "video_url", "")
    if not video_url and is_youtube_link(link):
        video_url = link
    video_id = extract_youtube_video_id(video_url) or extract_youtube_video_id(link) or get_entry_value(entry, "video_id", "")
    return {
        "title": get_entry_value(entry, "title", "").strip(),
        "summary": get_entry_value(entry, "summary", "") or get_entry_value(entry, "description", ""),
        "description": get_entry_value(entry, "description", ""),
        "link": link,
        "id": get_entry_value(entry, "id", ""),
        "source": source,
        "published": get_entry_value(entry, "published", "") or get_entry_value(entry, "updated", "") or get_entry_value(entry, "pubDate", ""),
        "video_url": video_url,
        "video_id": video_id,
    }


def fetch_feed_entries(feed_url: str) -> list[Any]:
    """Download and parse a feed URL using requests and feedparser."""
    response = requests.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    parsed_feed = feedparser.parse(response.content)
    return parsed_feed.entries


def is_live_match_entry(entry: dict[str, Any]) -> bool:
    """Heuristic to detect if a feed entry refers to a live match or live event for priority competitions."""
    title = normalize_text(entry.get("title", ""))
    if not title:
        return False

    # Priority competition mentions
    if "fifa world cup" in title or "copa do mundo" in title or "fifa" in title:
        # If it mentions live or contains a score pattern it's likely live
        if "live" in title or re.search(r"\b\d+-\d+\b", title) or "ft" in title:
            return True

    # Generic live indicators
    live_indicators = ["live", "live commentary", "liveblog", "minute", "'", "\baos\b"]
    if any(ind in title for ind in live_indicators):
        # require also a score or a vs separator to reduce false positives
        if re.search(r"\b\d+-\d+\b", title) or re.search(r"\bvs\b|\bx\b|\b-\b", title):
            return True

    return False


def find_live_matches_from_feeds() -> list[dict[str, Any]]:
    """Scan configured feeds for likely live matches; returns a list of matching entries.

    This is a heuristic scan: it looks for entries with 'live' indicators, score patterns,
    or explicit mentions of FIFA World Cup / Copa do Mundo.
    """
    matches: list[dict[str, Any]] = []
    feeds = get_feeds()
    for source_name, feed_url in feeds.items():
        try:
            entries = fetch_feed_entries(feed_url)
        except Exception:
            continue

        for raw in entries:
            normalized = normalize_entry(raw, source_name)
            if is_live_match_entry(normalized):
                matches.append(normalized)

    return matches


def is_live_goal_event(article: dict[str, str]) -> bool:
    """Detect likely live football goal events from headlines or source metadata."""
    title = normalize_text(article.get("title", ""))
    if not title:
        return False

    live_markers = ["goal", "golaço", "gol", "scores", "scored", "penalty", "free kick", "red card", "own goal"]
    if not any(marker in title for marker in live_markers):
        return False

    involved_terms = [
        "brazil",
        "argentina",
        "messi",
        "cristiano ronaldo",
        "neymar",
        "vinicius",
        "endrick",
        "mbappé",
        "mbappe",
        "haaland",
        "cape verde",
    ]
    return any(term in title for term in involved_terms)


def build_search_links(grouped_article: dict[str, Any]) -> list[str]:
    """Create fallback search links for official video discovery."""
    title = grouped_article.get("title", "")
    sources = grouped_article.get("sources", [])
    source_hint = " ".join(str(source) for source in sources if str(source))
    query = f"{title} {source_hint}".strip()
    searches = [
        f"https://www.youtube.com/results?search_query={requests.utils.quote(query)}",
        f"https://www.google.com/search?q={requests.utils.quote(query + ' official clip')}",
    ]
    if "FIFA" in source_hint:
        searches.append(f"https://www.google.com/search?q={requests.utils.quote(query + ' FIFA official')}")
    if "ESPN" in source_hint:
        searches.append(f"https://www.google.com/search?q={requests.utils.quote(query + ' ESPN official')}")
    if "BBC" in source_hint:
        searches.append(f"https://www.google.com/search?q={requests.utils.quote(query + ' BBC Sport official')}")
    return searches


def automatic_video_status_line(grouped_article: dict[str, Any]) -> str:
    """Return a Telegram line describing TVNZ-only automatic video status."""
    status = str(grouped_article.get("automatic_video_status") or "").strip()
    return f"*🎥 {status}*\n" if status else ""


def build_content_discovery_telegram_message(grouped_article: dict[str, Any], config: dict[str, str]) -> str:
    """Build a discovery-first Telegram alert for football Shorts production."""
    shorts_pack = build_portuguese_shorts_pack(grouped_article, config)
    article_links = [str(link) for link in grouped_article.get("links", []) if str(link)]
    if not article_links and grouped_article.get("link"):
        article_links = [str(grouped_article.get("link"))]
    video_links = [str(link) for link in grouped_article.get("video_links", []) if str(link)]
    if grouped_article.get("video_url") and str(grouped_article.get("video_url")) not in video_links:
        video_links.append(str(grouped_article.get("video_url")))
    official_sources = [str(source) for source in sorted(grouped_article.get("sources", []), key=get_source_priority) if str(source)]

    title = grouped_article.get("title", "") or "Nova história de futebol"
    summary = grouped_article.get("summary", "") or grouped_article.get("description", "") or "História em alta no futebol"
    reason = grouped_article.get("reason", "") or shorts_pack.get('viral_reason', "")
    viral_score = grouped_article.get("viral_score", shorts_pack.get('viral_score', 0)) or 0
    content_score = grouped_article.get("score", 0) or 0
    if isinstance(content_score, (int, float)) and 0 <= float(content_score) <= 10:
        score_label = str(int(round(float(content_score) * 10)))
    elif isinstance(viral_score, (int, float)):
        score_label = str(max(0, min(100, int(viral_score))))
    else:
        score_label = str(viral_score)

    article_links_block = "\n".join(f"- {link}" for link in article_links) if article_links else "- Não informado"
    video_links_block = "\n".join(f"- {link}" for link in video_links) if video_links else "- Não informado"
    video_summary = " | ".join(video_links) if video_links else "aguardando TVNZ Sport"
    official_sources_block = ", ".join(official_sources) if official_sources else "Não informado"
    hashtags_block = " ".join(shorts_pack.get('hashtags', [])) or "#Futebol #ShortsFutebol"
    scripts = shorts_pack.get('narration_scripts', {}) or {}

    return (
        f"🚨 {title}\n\n"
        f"📰 Notícias\n"
        f"✅ {official_sources_block}\n\n"
        f"🎥 Vídeos\n"
        f"▶ {video_summary}\n"
        f"{automatic_video_status_line(grouped_article)}\n"
        f"🔥 Viral Score: {score_label}/100\n\n"
        f"🧠 Resumo da história: {summary}\n"
        f"📈 Por que está explodindo: {reason}\n\n"
        f"🔗 Links para todos os artigos:\n{article_links_block}\n\n"
        f"🔗 Links para todos os vídeos:\n{video_links_block}\n\n"
        f"🎙 HeyGen\n"
        f"{shorts_pack.get('heygen_narration', '')}\n\n"
        f"📝 Shorts\n"
        f"{shorts_pack.get('shorts_title', '')}\n\n"
        f"📸 Thumbnail\n"
        f"{', '.join(shorts_pack.get('thumbnail_text', []))}\n\n"
        f"🎙 30s: {scripts.get('30s', '')}\n"
        f"🎙 45s: {scripts.get('45s', '')}\n"
        f"🎙 60s: {scripts.get('60s', '')}\n\n"
        f"👉 CTA: {shorts_pack.get('suggested_cta', '')}\n\n"
        f"🏷 {hashtags_block}"
    ).strip()


def build_live_event_telegram_message(grouped_article: dict[str, Any], config: dict[str, str]) -> str:
    """Build a Brazilian Portuguese Telegram alert for an urgent live football goal event."""
    title = grouped_article.get("title", "")
    shorts_pack = build_portuguese_shorts_pack(grouped_article, config)
    match = grouped_article.get("match") or grouped_article.get("title", "")
    minute = grouped_article.get("minute") or ""
    scorer = grouped_article.get("goal_scorer") or grouped_article.get("scorer") or ""
    competition = grouped_article.get("competition") or ""
    official_source = grouped_article.get("official_source") or ", ".join(grouped_article.get("sources", []))

    return (
        "*⚡ Alerta ao Vivo — Futebol em tempo real*\n\n"
        f"*⚽ Match:* {match}\n"
        f"*⏱ Minute:* {minute}\n"
        f"*🥅 Goal scorer:* {scorer or 'Não informado'}\n"
        f"*🏆 Competition:* {competition or 'Não informado'}\n"
        f"*📺 Official source:* {official_source}\n"
        f"{automatic_video_status_line(grouped_article)}"
        f"*🎬 Shorts title:* {shorts_pack.get('shorts_title', '')}\n"
        f"*📝 Short description:* {shorts_pack.get('description', '')}\n"
        f"*🎙 30-second script:* {shorts_pack.get('narration_scripts', {}).get('30s', '')}\n"
        f"*🤖 HeyGen narration:* {shorts_pack.get('heygen_narration', '')}"
    ).strip()


def build_generic_news_telegram_message(grouped_article: dict[str, Any], config: dict[str, str]) -> str:
    """Build a compact Telegram alert for general football news."""
    shorts_pack = build_portuguese_shorts_pack(grouped_article, config)
    sources = ", ".join(str(source) for source in grouped_article.get("sources", []) if str(source))
    links = [str(link) for link in grouped_article.get("links", []) if str(link)]
    original_url = grouped_article.get("link") or (links[0] if links else "")
    why_it_matters = (
        grouped_article.get("reason")
        or grouped_article.get("summary")
        or grouped_article.get("description")
        or shorts_pack.get('viral_reason', "")
    )
    why_it_matters = public_telegram_text(why_it_matters)
    return (
        "*📰 Alerta de notícia*\n\n"
        f"*Título:* {grouped_article.get('title', '')}\n"
        f"*Fonte:* {sources or grouped_article.get('source', 'Não informado')}\n"
        f"*Link original:* {original_url}\n"
        f"{automatic_video_status_line(grouped_article)}"
        f"*Por que importa:* {why_it_matters}\n"
        f"*Ideia para Shorts:* {shorts_pack.get('shorts_title', '')}"
    ).strip()


def build_transfer_telegram_message(grouped_article: dict[str, Any], config: dict[str, str]) -> str:
    """Build a compact Telegram alert for transfer news."""
    shorts_pack = build_portuguese_shorts_pack(grouped_article, config)
    sources = ", ".join(str(source) for source in grouped_article.get("sources", []) if str(source))
    links = [str(link) for link in grouped_article.get("links", []) if str(link)]
    original_url = grouped_article.get("link") or (links[0] if links else "")
    player_or_club = (
        grouped_article.get("player")
        or grouped_article.get("club")
        or grouped_article.get("transfer_player")
        or grouped_article.get("transfer_club")
        or "Não informado"
    )
    return (
        "*🔁 Alerta de transferência*\n\n"
        f"*Jogador/Clube:* {player_or_club}\n"
        f"*Título:* {grouped_article.get('title', '')}\n"
        f"*Fonte:* {sources or grouped_article.get('source', 'Não informado')}\n"
        f"*Link original:* {original_url}\n"
        f"{automatic_video_status_line(grouped_article)}"
        f"*Ideia para Shorts:* {shorts_pack.get('shorts_title', '')}"
    ).strip()


def build_manual_telegram_message(grouped_article: dict[str, Any], config: dict[str, str]) -> str:
    """Build a Brazilian Portuguese Telegram alert for a manual breaking-news trigger."""
    shorts_pack = build_portuguese_shorts_pack(grouped_article, config)
    article_links = grouped_article.get("links") or [""]
    original_url = grouped_article.get("link") or article_links[0]
    return (
        "*⚡ Alerta Manual — Breaking News*\n\n"
        f"*🚨 Viral Score:* {shorts_pack.get('score', 0)}/10\n"
        f"*📺 Source:* {', '.join(grouped_article.get('sources', []))}\n"
        f"*🔗 Original URL:* {original_url}\n"
        f"*🎥 YouTube URL:* {grouped_article.get('video_url', '')}\n"
        f"{automatic_video_status_line(grouped_article)}"
        f"*🎬 Shorts title:* {shorts_pack.get('shorts_title', '')}\n"
        f"*🖼 Thumbnail text:* {', '.join(shorts_pack.get('thumbnail_text', []))}\n"
        f"*🎙 30s script:* {shorts_pack.get('narration_scripts', {}).get('30s', '')}\n"
        f"*🤖 HeyGen narration:* {shorts_pack.get('heygen_narration', '')}\n"
        f"*📝 YouTube description:* {shorts_pack.get('description', '')}\n"
        f"*🏷 Hashtags:* {' '.join(shorts_pack.get('hashtags', []))}\n"
        f"*🔍 Search keywords:* {', '.join(shorts_pack.get('search_keywords', []))}"
    ).strip()


def build_portuguese_telegram_message(grouped_article: dict[str, Any], config: dict[str, str]) -> str:
    """Build a Brazilian Portuguese Telegram alert for football videos, especially CazéTV Shorts."""
    title = grouped_article.get("title", "")
    sources = ", ".join(grouped_article.get("sources", []))
    links = "\n".join(grouped_article.get("links", []))

    original_video_url = grouped_article.get("video_url") or grouped_article.get("link") or links or ""
    original_search_keywords = grouped_article.get("search_keywords") or []
    video_status = str(grouped_article.get("video_status", "") or "").strip().lower()

    shorts_pack = build_portuguese_shorts_pack(grouped_article, config)
    video_url = original_video_url or grouped_article.get("video_url") or shorts_pack.get('video_url', "")
    search_keywords = original_search_keywords or grouped_article.get("search_keywords") or shorts_pack.get('search_keywords', [])

    warning_block = ""
    if video_status in {"unavailable", "region_blocked", "blocked", "region-blocked"}:
        warning_block = "\n⚠️ Este vídeo pode estar bloqueado na sua região."

    message = (
        "*⚡ Alerta de Conteúdo — CazéTV / Futeba & Juninho*\n\n"
        f"*📺 Fonte:* {sources}\n"
        f"*📰 Título original:* {title}\n"
        f"*🔗 Link do vídeo:* {video_url}\n"
        f"{warning_block}\n"
        f"{automatic_video_status_line(grouped_article)}"
        f"*🔍 Palavras-chave de busca:* {', '.join(search_keywords) if search_keywords else ', '.join(shorts_pack.get('search_keywords', []))}\n"
        f"*🎬 Shorts title:* {shorts_pack.get('shorts_title', '')}\n"
        f"*📝 Descrição:* {shorts_pack.get('description', '')}\n"
        f"*🎙 30s script:* {shorts_pack.get('narration_scripts', {}).get('30s', '')}\n"
        f"*🤖 HeyGen narration:* {shorts_pack.get('heygen_narration', '')}\n"
        f"*🖼 Thumbnail text:* {', '.join(shorts_pack.get('thumbnail_text', []))}\n"
        f"*🏷 Hashtags:* {' '.join(shorts_pack.get('hashtags', []))}\n"
        f"*🔥 Viral Score:* {shorts_pack.get('score', 0)}/10"
    ).strip()
    return message


def send_telegram_notification(grouped_article: dict[str, Any], config: dict[str, str]) -> bool:
    """Send a Telegram message for a high-score grouped article if credentials are configured."""
    token = config.get("telegram_bot_token", "")
    chat_id = config.get("telegram_chat_id", "")
    notification_title = str(grouped_article.get("title", "") or "").strip()
    notification_links = grouped_article.get("links") or []
    notification_url = str(
        grouped_article.get("video_url") or grouped_article.get("link")
        or (notification_links[0] if notification_links else "") or ""
    ).strip()
    notification_source = str(
        grouped_article.get("source")
        or next(iter(grouped_article.get("sources") or []), "Unknown")
    )
    notification_id = normalize_media_id(notification_url)
    if not token or not chat_id:
        logger.warning("Telegram credentials are not configured. Skipping notification for %s", notification_title)
        return False
    if notification_id and not should_send_once(
        notification_id, notification_url, notification_title, notification_source, "alert", config,
    ):
        return False

    source_for_classification = grouped_article.get("official_source", "") or next(
        (str(source) for source in grouped_article.get("sources", []) if str(source)),
        str(grouped_article.get("source", "")),
    )
    category = str(grouped_article.get("content_category", "") or "").strip()
    if not category:
        category = str(classify_story_content(notification_title, source_for_classification, grouped_article.get("match")).get("category", "UNKNOWN"))
    live_template_categories = {
        "GOAL_CLIP",
        "VAR_OR_PENALTY",
        "RED_CARD",
        "SHOOTOUT",
        "MATCH_HIGHLIGHT",
    }

    if grouped_article.get("is_manual_event"):
        message = build_manual_telegram_message(grouped_article, config)
    elif category == "GENERAL_NEWS":
        message = build_generic_news_telegram_message(grouped_article, config)
    elif category == "TRANSFER_NEWS":
        message = build_transfer_telegram_message(grouped_article, config)
    elif category in live_template_categories and (grouped_article.get("is_live_event") or any(
        is_live_goal_event({"title": grouped_article.get("title", ""), "source": source})
        for source in grouped_article.get("sources", [])
    )):
        message = build_live_event_telegram_message(grouped_article, config)
    elif grouped_article.get("sources") and any(source == "CazéTV" for source in grouped_article.get("sources", [])):
        message = build_portuguese_telegram_message(grouped_article, config)
    elif float(grouped_article.get("viral_score", 0) or 0) >= 75:
        message = build_content_discovery_telegram_message(grouped_article, config)
    else:
        sources = ", ".join(grouped_article.get("sources", []))
        links = "\n".join(grouped_article.get("links", []))
        shorts_pack = build_portuguese_shorts_pack(grouped_article, config)
        heygen_prompt = (
            f"Persona: apresentador brasileiro de futebol, energia alta, olhar direto, movimentos naturais. "
            f"Contexto: {notification_title}. "
            "Tom: empolgação, drama, humor leve, voz firme. "
            "Câmera: plano médio, gesto de apoio, pequenas pausas, expressão intensa."
        )
        veo_prompt = (
            f"Criar cenas extras para um Shorts de futebol sobre {notification_title}. "
            "Estilo cinematográfico, cortes rápidos, reação de torcida, close em jogador, câmera dinâmica, iluminação forte, sensação de explosão."
        )
        downloaded_video_path = grouped_article.get("downloaded_video_path", "")
        download_block = f"*💾 Vídeo baixado:* {downloaded_video_path}\n" if downloaded_video_path else ""
        article_links = grouped_article.get("links") or [""]
        original_url = grouped_article.get("link") or article_links[0]
        score = shorts_pack.get("score", 0)
        video_url = shorts_pack.get("video_url", "")
        thumbnail_frame_idea = shorts_pack.get("thumbnail_frame_idea", "")
        heygen_narration = shorts_pack.get("heygen_narration", "")
        description = shorts_pack.get("description", "")
        hashtags = " ".join(shorts_pack.get("hashtags", []))
        shorts_title = shorts_pack.get("shorts_title", "")
        scripts = shorts_pack.get("narration_scripts", {})
        script_30s = scripts.get("30s", "")
        script_45s = scripts.get("45s", "")
        script_60s = scripts.get("60s", "")
        search_keywords = ", ".join(shorts_pack.get("search_keywords", []))
        viral_reason = shorts_pack.get("viral_reason", "")

        message = (
            "*⚡ Alerta de Conteúdo — Futeba & Juninho*\n\n"
            f"*🚨 Viral Score:* {score}/10\n"
            f"*📺 Source:* {sources}\n"
            f"*🔗 Original URL:* {original_url}\n"
            f"*🎥 Link do vídeo oficial:* {video_url}\n"
            f"{automatic_video_status_line(grouped_article)}"
            f"{download_block}"
            f"*📰 Notícia:* {notification_title}\n"
            f"*🖼 Melhor thumbnail:* {thumbnail_frame_idea}\n"
            f"*🎙 Narração HeyGen:* {heygen_narration}\n"
            f"*📜 Prompt para HeyGen:* {heygen_prompt}\n"
            f"*🎬 Prompt para Veo 3/Kling:* {veo_prompt}\n"
            f"*📝 Descrição YouTube:* {description}\n"
            f"*🏷 Hashtags:* {hashtags}\n"
            f"*📌 Título:* {shorts_title}\n"
            f"*⏱ Tempo estimado:* 30s, 45s, 60s\n"
            f"*🎙 30s:* {script_30s}\n"
            f"*🎙 45s:* {script_45s}\n"
            f"*🎙 60s:* {script_60s}\n"
            f"*🔍 Search keywords:* {search_keywords}\n"
            f"*🔥 Potencial viral:* {score}/10\n"
            f"*💡 Por que vale postar:* {viral_reason}\n"
            f"*📰 Link da notícia:* {links}\n"
            f"*Source:* {sources}"
        ).strip()
    message = prepare_telegram_message(grouped_article, message).replace(token, "[REDACTED]")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    message_preview = message.replace(token, "[REDACTED]")[:500]
    logger.info("Telegram message (first 500 characters):\n%s", message_preview)

    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        status_code = response.status_code
        if status_code != 200:
            response_body = str(response.text or "").replace(token, "[REDACTED]")
            logger.error("Telegram %s\nResponse:\n%s", status_code, response_body)
            release_send_reservation(config, notification_id)
            return False

    except requests.exceptions.RequestException:
        release_send_reservation(config, notification_id)
        logger.error("Telegram request failed before a valid response was received for %s", notification_title)
        return False
    except Exception:
        release_send_reservation(config, notification_id)
        logger.error("Unexpected Telegram failure for %s", notification_title)
        return False

    logger.info("Telegram notification sent for %s", notification_title)
    if notification_id:
        mark_as_sent(
            config, "sent_alerts", notification_id,
            notification_source, notification_title, notification_url,
        )
    return True


def build_downloaded_video_caption(story: dict[str, Any]) -> str:
    """Build a compact caption for a downloaded video sent to Telegram."""
    if story.get("telegram_caption"):
        return str(story["telegram_caption"])[:1024]
    sources = ", ".join(str(source) for source in story.get("sources", []) if str(source))
    original_url = story.get("link") or next((link for link in story.get("links", []) if link), "")
    short_file_name = Path(str(
        story.get("vertical_short_path")
        or story.get("moments_clip_path")
        or story.get("downloaded_video_path")
        or ""
    )).name
    metadata_file_name = Path(str(story.get("short_metadata_path") or "")).name
    duration_seconds = story.get("final_video_duration_seconds") or story.get("moments_duration_seconds")
    if isinstance(duration_seconds, (int, float)) and duration_seconds > 0:
        duration_text = f"{float(duration_seconds):.1f}s"
    else:
        duration_text = "unknown"
    file_size_bytes = story.get("final_video_file_size_bytes")
    if isinstance(file_size_bytes, (int, float)) and file_size_bytes >= 0:
        file_size_text = f"{float(file_size_bytes) / (1024 * 1024):.2f} MB"
    else:
        file_size_text = "unknown"
    lines = [
        f"Title: {story.get('title', '')}",
        f"Source: {sources or story.get('source', '') or story.get('official_source', '')}",
        f"Category: {story.get('content_category', '') or 'UNKNOWN'}",
        f"Duration: {duration_text}",
        f"File size: {file_size_text}",
    ]
    if short_file_name:
        lines.append(f"Short file: {short_file_name}")
    if metadata_file_name:
        lines.append(f"Metadata: {metadata_file_name}")
    if original_url:
        if story.get("platform") == "Instagram" or "instagram.com" in str(original_url).casefold():
            lines.append(f"Link original: {original_url}")
        else:
            lines.append(f"Original URL: {original_url}")
    return "\n".join(lines)[:1024]


def _shorten_short_text(value: Any, limit: int = 55) -> str:
    """Return one-line overlay text that fits a vertical short."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 3)].rstrip(" .,;:-") + "..."


def _escape_drawtext_text(value: Any) -> str:
    """Escape text for ffmpeg drawtext text='...' filter arguments."""
    text = re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()
    text = text.replace("\\", "\\\\")
    text = text.replace("'", r"\'")
    text = text.replace(":", r"\:")
    text = text.replace(",", r"\,")
    text = text.replace("%", r"\%")
    return text


def _extract_teams_from_title(title: str) -> list[str]:
    """Best-effort team extraction from common football title phrasing."""
    clean_title = re.sub(r"\b(?:match|extended)?\s*highlights?\b", " ", str(title or ""), flags=re.IGNORECASE)
    clean_title = re.sub(r"\s+", " ", clean_title).strip(" -|")
    match = re.search(r"\b([A-Z][A-Za-zÀ-ÿ .'-]{2,35})\s+(?:vs\.?|v\.?|versus|against)\s+([A-Z][A-Za-zÀ-ÿ .'-]{2,35})", clean_title)
    if not match:
        return []
    teams = []
    for value in match.groups():
        team = re.sub(r"\s+", " ", value).strip(" -|:.,")
        team = re.split(
            r"\b(?:dramatic|late|winner|goal|goals|score|scores|scored|in|at|after|before|highlights?)\b",
            team,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" -|:.,")
        if team and len(team) <= 35:
            teams.append(team)
    return teams


def _hashtag_from_team(team: str) -> str:
    tag = re.sub(r"[^A-Za-z0-9À-ÿ]", "", team)
    return f"#{tag}" if tag else ""


def find_short_font_path() -> Path | None:
    """Find a font that ffmpeg drawtext can use on Linux or Windows."""
    candidates = []
    env_font_path = os.getenv("SHORTS_FONT_PATH")
    if env_font_path:
        candidates.append(env_font_path)
    candidates.extend(DEFAULT_SHORTS_FONT_PATHS)

    for candidate in candidates:
        font_path = Path(candidate)
        if font_path.exists() and font_path.is_file():
            return font_path
    return None


def _format_drawtext_font_path(font_path: str | Path) -> str:
    """Normalize font path separators for ffmpeg drawtext."""
    normalized = str(font_path).replace("\\", "/")
    normalized = normalized.replace("'", r"\'")
    if re.match(r"^[A-Za-z]:/", normalized):
        normalized = normalized[:1] + r"\:" + normalized[2:]
    return normalized


def _drawtext_filter(text: str, x: str, y: str, fontsize: int, font_path: str | Path, boxcolor: str = "black@0.55", enable: str = "") -> str:
    return (
        "drawtext="
        f"fontfile='{_format_drawtext_font_path(font_path)}':"
        f"text='{_escape_drawtext_text(text)}':"
        f"x={x}:y={y}:"
        f"fontsize={fontsize}:fontcolor=white:"
        "borderw=2:bordercolor=black@0.45:"
        f"box=1:boxcolor={boxcolor}:boxborderw=28"
        + (f":enable='{enable}'" if enable else "")
    )


TELEGRAM_SHORT_EFFECTS = {
    "zoom", "headline", "cta", "freeze", "replay",
    "ticker", "slide_text", "impact_words", "pulse_text", "player_tag", "blink_text",
}
DEFAULT_TELEGRAM_SHORT_EFFECTS = ["zoom", "headline", "cta"]


def parse_telegram_short_request(caption: str) -> dict[str, Any]:
    """Parse `/short duration=N effects=a,b title=...` caption options."""
    text = str(caption or "").strip()
    duration_match = re.search(r"(?:^|\s)duration=(\d+)", text, re.IGNORECASE)
    effects_match = re.search(r"(?:^|\s)effects=([^\s]+)", text, re.IGNORECASE)
    zoom_match = re.search(r"(?:^|\s)zoom=([^\s]+)", text, re.IGNORECASE)
    focus_match = re.search(r"(?:^|\s)focus=([^\s]+)", text, re.IGNORECASE)
    focus_x_match = re.search(r"(?:^|\s)focus_x=([^\s]+)", text, re.IGNORECASE)
    focus_y_match = re.search(r"(?:^|\s)focus_y=([^\s]+)", text, re.IGNORECASE)
    quality_match = re.search(r"(?:^|\s)quality=([^\s]+)", text, re.IGNORECASE)
    title_match = re.search(
        r"(?:^|\s)title=(.+?)(?=\s+(?:duration|effects|zoom|focus|focus_x|focus_y|quality|cta|force|reprocess|ticker|phrase|words|textspeed|textpos|blink_text|caps|blink_position|blink_style|blink_start|blink_end)=|$)",
        text, re.IGNORECASE,
    )
    cta_match = re.search(r"(?:^|\s)cta=([^\s]+)", text, re.IGNORECASE)
    force_match = re.search(r"(?:^|\s)(?:force|reprocess)=(true|1|yes|on)(?:\s|$)", text, re.IGNORECASE)
    def option_value(name: str) -> str:
        match = re.search(rf'(?:^|\s){name}=(?:"([^"]*)"|\'([^\']*)\'|([^\s]+))', text, re.IGNORECASE)
        return next((group for group in match.groups() if group is not None), "") if match else ""
    parsed_title = title_match.group(1).strip() if title_match else "LANCE DO JOGO"
    if len(parsed_title) >= 2 and parsed_title[0] == parsed_title[-1] and parsed_title[0] in {'"', "'"}:
        parsed_title = parsed_title[1:-1].strip()
    effects = DEFAULT_TELEGRAM_SHORT_EFFECTS.copy()
    if effects_match:
        aliases = {"impact": "impact_words", "slide": "slide_text", "pulse": "pulse_text", "texto_piscante": "blink_text"}
        effects = []
        for raw_effect in effects_match.group(1).split(","):
            effect = aliases.get(raw_effect.casefold(), raw_effect.casefold())
            if effect in TELEGRAM_SHORT_EFFECTS:
                effects.append(effect)
    zoom_intensity = 1.25
    zoom_mode = "smart"
    if zoom_match:
        zoom_value = zoom_match.group(1).casefold()
        named_zoom = {"light": 1.10, "medium": 1.20, "strong": 1.35, "verystrong": 1.50}
        mode_aliases = {"normal": "smart", "strong": "smart_strong", "smart": "smart", "smart_strong": "smart_strong", "center": "center"}
        zoom_mode = mode_aliases.get(zoom_value, "smart")
        try:
            if zoom_value in {"normal", "smart", "center"}:
                zoom_intensity = 1.25
            elif zoom_value == "smart_strong":
                zoom_intensity = 1.35
            else:
                zoom_intensity = named_zoom[zoom_value] if zoom_value in named_zoom else float(zoom_value)
        except ValueError:
            logger.warning("Invalid zoom intensity %s; using 1.25x", zoom_value)
        zoom_intensity = min(1.50, max(1.0, zoom_intensity))
    focus_mode = str(focus_match.group(1) if focus_match else "action").casefold()
    focus_presets = {
        "left": (0.30, 0.60), "center": (0.50, 0.60), "right": (0.70, 0.60),
        "bottom": (0.50, 0.75), "player_left": (0.35, 0.70), "player_right": (0.65, 0.70),
        "goal_left": (0.30, 0.45), "goal_right": (0.70, 0.45),
    }
    allowed_focus_modes = {"manual", "smart", "player", "ball", "goal", "action", *focus_presets}
    if focus_mode not in allowed_focus_modes:
        focus_mode = "action"
    manual_focus_valid = True
    focus_x = focus_y = None
    if focus_mode in focus_presets:
        focus_x, focus_y = focus_presets[focus_mode]
    elif focus_mode == "manual":
        try:
            focus_x = float(focus_x_match.group(1)) if focus_x_match else 0.5
            focus_y = float(focus_y_match.group(1)) if focus_y_match else 0.5
            manual_focus_valid = 0.0 <= focus_x <= 1.0 and 0.0 <= focus_y <= 1.0
        except (TypeError, ValueError):
            manual_focus_valid = False
        if not manual_focus_valid:
            logger.warning("Invalid manual focus coordinates; falling back to center")
            focus_x, focus_y = 0.5, 0.5
    ticker_text = option_value("ticker") or option_value("phrase")
    words_text = option_value("words")
    if "impact_words" in effects and not words_text and ticker_text and "|" in ticker_text:
        words_text = ticker_text
    if "ticker" in effects and not ticker_text:
        ticker_text = parsed_title or "OLHA ESSE LANCE"
    impact_words = [word.strip() for word in words_text.split("|") if word.strip()]
    if "impact_words" in effects and not impact_words:
        impact_words = ["GOL!", "VAR?", "POLÊMICA!", "OLHA ISSO!"]
    text_speed = option_value("textspeed").casefold()
    text_position = option_value("textpos").casefold()
    blink_text = option_value("blink_text")
    caps_value = option_value("caps").casefold()
    blink_position = option_value("blink_position").casefold()
    blink_style = option_value("blink_style").casefold()
    try:
        blink_start = max(0.0, float(option_value("blink_start") or 0))
        blink_end = max(blink_start, float(option_value("blink_end") or 5))
    except ValueError:
        blink_start, blink_end = 0.0, 5.0
    request = {
        "duration": min(60, max(1, int(duration_match.group(1)))) if duration_match else 20,
        "effects": list(dict.fromkeys(effects)),
        "title": parsed_title,
        "zoom_intensity": zoom_intensity,
        "zoom_mode": zoom_mode,
        "focus_mode": focus_mode,
        "focus_x": focus_x,
        "focus_y": focus_y,
        "manual_focus_valid": manual_focus_valid,
        "quality": str(quality_match.group(1) if quality_match else "standard").casefold(),
        "cta": ((option_value("cta") or cta_match.group(1)).replace("_", " ") if cta_match else "COMENTA AÍ 👇"),
        "force": bool(force_match),
        "ticker_text": ticker_text,
        "impact_words": impact_words,
        "text_speed": text_speed if text_speed in {"slow", "medium", "fast"} else "medium",
        "text_position": text_position if text_position in {"top", "middle", "bottom"} else "bottom",
        "blink_text": blink_text,
        "caps": caps_value not in {"false", "0", "no", "off"},
        "blink_position": blink_position if blink_position in {"top", "middle", "bottom"} else "middle",
        "blink_style": blink_style if blink_style in {"blink", "pulse", "impact"} else "blink",
        "blink_start": blink_start, "blink_end": blink_end,
    }
    logger.info("Parsed short request: %s", request)
    logger.info("Requested effects: %s", request["effects"])
    logger.info("Parsed ticker text: %s", request["ticker_text"])
    logger.info("Parsed impact words: %s", request["impact_words"])
    logger.info("Parsed focus mode: %s", request["focus_mode"])
    logger.info("Parsed focus_x/focus_y: %s, %s", request["focus_x"], request["focus_y"])
    logger.info("Parsed zoom value: %.2fx", request["zoom_intensity"])
    return request


def telegram_processing_key(file_unique_id: str, request: dict[str, Any]) -> str:
    """Return a stable dedupe key for one Telegram file plus its edit options."""
    raw_cta = str(request.get("cta") or "")
    ascii_cta = unicodedata.normalize("NFKD", raw_cta).encode("ascii", "ignore").decode("ascii")
    ascii_cta = re.sub(r"[^A-Za-z0-9]+", "_", ascii_cta).strip("_") or "COMENTA_AI"
    options = {
        "duration": request.get("duration"),
        "effects": sorted(str(effect) for effect in request.get("effects", [])),
        "title": str(request.get("title") or "").strip(),
        "zoom": f"{float(request.get('zoom_intensity') or 1.25):.2f}",
        "zoom_mode": request.get("zoom_mode") or "smart",
        "focus_mode": request.get("focus_mode") or "action",
        "focus_x": request.get("focus_x"), "focus_y": request.get("focus_y"),
        "cta": ascii_cta,
        "ticker": str(request.get("ticker_text") or "").strip(),
        "impact_words": list(request.get("impact_words") or []),
        "text_speed": request.get("text_speed"),
        "text_position": request.get("text_position"),
        "blink_text": request.get("blink_text"), "caps": request.get("caps"),
        "blink_position": request.get("blink_position"), "blink_style": request.get("blink_style"),
        "blink_start": request.get("blink_start"), "blink_end": request.get("blink_end"),
    }
    safe = lambda value: re.sub(r"\s+", " ", str(value or "").strip()).replace(":", r"\:")
    return "telegram_short:" + ":".join([
        safe(file_unique_id), safe(options["duration"]), safe(",".join(options["effects"])),
        safe(options["title"]), safe(options["zoom"] + "_" + str(options["zoom_mode"]) + "_" + str(options["focus_mode"]) + f"_{options['focus_x']}_{options['focus_y']}"), safe(options["cta"]), safe(options["ticker"] + "_" + str(options["blink_text"]) + "_" + str(options["caps"]) + "_" + str(options["blink_position"]) + "_" + str(options["blink_style"]) + f"_{options['blink_start']}_{options['blink_end']}"),
    ])


def smooth_focus_points(points: list[tuple[float, float]], max_step: float = 0.08) -> tuple[float, float] | None:
    """Smooth normalized focus samples and clamp sudden movement."""
    if not points:
        return None
    x, y = points[0]
    smoothed = [(x, y)]
    for target_x, target_y in points[1:]:
        dx = max(-max_step, min(max_step, target_x - x))
        dy = max(-max_step, min(max_step, target_y - y))
        x += dx * 0.35
        y += dy * 0.35
        smoothed.append((x, y))
    weights = range(1, len(smoothed) + 1)
    total = sum(weights)
    return (
        sum(point[0] * weight for point, weight in zip(smoothed, weights)) / total,
        sum(point[1] * weight for point, weight in zip(smoothed, weights)) / total,
    )


def detect_motion_focus_point(video_path: str | Path, focus_mode: str = "action") -> tuple[float, float] | None:
    """Estimate a stable normalized motion center from tiny grayscale frames."""
    width, height = 32, 18
    command = [
        "ffmpeg", "-v", "error", "-i", str(video_path), "-vf",
        f"fps=2,scale={width}:{height},format=gray", "-frames:v", "24",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=True, timeout=30)
        raw = bytes(getattr(result, "stdout", b"") or b"")
        frame_size = width * height
        frames = [raw[index:index + frame_size] for index in range(0, len(raw), frame_size) if len(raw[index:index + frame_size]) == frame_size]
        logger.info("Frame samples analyzed: %s", len(frames))
        points: list[tuple[float, float]] = []
        ball_points: list[tuple[float, float]] = []
        for previous, current in zip(frames, frames[1:]):
            changes = [abs(current[index] - previous[index]) for index in range(frame_size)]
            threshold = max(18, sum(changes) / frame_size * 1.8)
            active = [
                (index % width, index // width, value) for index, value in enumerate(changes)
                if value >= threshold and index // width >= 3
            ]
            weight = sum(value for _, _, value in active)
            if weight > 1000:
                points.append((
                    sum(x * value for x, _, value in active) / weight / (width - 1),
                    sum(y * value for _, y, value in active) / weight / (height - 1),
                ))
            bright_fast = [
                (index % width, index // width, changes[index]) for index, value in enumerate(current)
                if value >= 210 and changes[index] >= max(35, threshold) and index // width >= 3
            ]
            bright_weight = sum(value for _, _, value in bright_fast)
            if 80 < bright_weight < 1800 and bright_fast:
                ball_points.append((
                    sum(x * value for x, _, value in bright_fast) / bright_weight / (width - 1),
                    sum(y * value for _, y, value in bright_fast) / bright_weight / (height - 1),
                ))
        if focus_mode == "ball":
            ball_focus = smooth_focus_points(ball_points)
            if ball_focus:
                points = ball_points
            else:
                logger.info("Ball focus unavailable; falling back to action focus")
        focus = smooth_focus_points(points)
        if focus_mode == "goal" and focus and 0.38 < focus[0] < 0.62:
            logger.info("Goal focus uncertain; falling back to action focus")
        if focus:
            logger.info("Motion focus point detected: x=%.3f, y=%.3f", focus[0], focus[1])
            focus = (min(0.78, max(0.22, focus[0])), min(0.60, max(0.25, focus[1] - 0.05)))
            logger.info("Smoothed focus point: x=%.3f, y=%.3f", focus[0], focus[1])
        return focus
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        logger.warning("Smart zoom analysis failed: %s", exc)
        return None


def probe_video_dimensions(video_path: str | Path) -> tuple[int, int] | None:
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(video_path),
        ], capture_output=True, text=True, check=True, timeout=15, **SUBPROCESS_TEXT_KWARGS)
        width, height = str(getattr(result, "stdout", "") or "").strip().split("x", 1)
        return int(width), int(height)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def build_short_metadata(story: dict[str, Any], vertical_path: str | Path) -> dict[str, Any]:
    """Build companion metadata for the generated vertical short."""
    raw_title = str(story.get("shorts_title") or story.get("title") or Path(vertical_path).stem).strip()
    base_title = re.sub(r"\b(?:match|extended)\s+highlights?\b", "Highlights", raw_title, flags=re.IGNORECASE)
    title = _shorten_short_text(base_title.rstrip("!?.") + "!", 70)
    sources = ", ".join(str(source) for source in story.get("sources", []) if str(source))
    source = sources or story.get("source") or story.get("official_source") or YOUTUBE_DOWNLOAD_CHANNEL
    original_url = (
        story.get("original_video_url")
        or story.get("video_url")
        or story.get("webpage_url")
        or story.get("link")
        or next((link for link in story.get("links", []) if link), "")
    )
    hook = story.get("short_hook") or "Qual foi o melhor momento?"
    hashtags = story.get("hashtags") or ["#Futebol", "#ShortsFutebol", "#WorldCup", "#Futeba"]
    if isinstance(hashtags, str):
        hashtags = [tag for tag in hashtags.split() if tag]
    else:
        hashtags = list(hashtags)
    for required_tag in ("#Futebol", "#ShortsFutebol", "#WorldCup", "#Futeba"):
        if required_tag not in hashtags:
            hashtags.append(required_tag)
    for team in _extract_teams_from_title(raw_title):
        team_tag = _hashtag_from_team(team)
        if team_tag and team_tag not in hashtags:
            hashtags.append(team_tag)
    hashtag_text = " ".join(str(tag) for tag in hashtags if str(tag))
    pinned_comment = story.get("pinned_comment") or "Qual foi o melhor momento? Comenta ai."
    description = (
        f"{hook}\n\n"
        f"Fonte: {source}\n"
        f"Original: {original_url or 'TVNZ Sport'}\n"
        f"Arquivo: {Path(vertical_path).name}\n\n"
        f"{hashtag_text}"
    )
    return {
        "title": title,
        "description": description,
        "hashtags": hashtags,
        "pinned_comment": pinned_comment,
    }


def _write_short_metadata_file(story: dict[str, Any], vertical_path: str | Path) -> None:
    metadata = build_short_metadata(story, vertical_path)
    metadata_path = Path(vertical_path).with_suffix(".txt")
    hashtags = " ".join(str(tag) for tag in metadata.get("hashtags", []) if str(tag))
    metadata_path.write_text(
        "\n".join([
            f"Title: {metadata.get('title', '')}",
            "",
            "Description:",
            str(metadata.get("description", "")),
            "",
            f"Hashtags: {hashtags}",
            "",
            f"Pinned comment: {metadata.get('pinned_comment', '')}",
            f"Input file: {story.get('short_input_file', '')}",
            f"Output file: {vertical_path}",
            f"Requested effects: {', '.join(story.get('requested_effects', []))}",
            f"Applied effects: {', '.join(story.get('applied_effects', []))}",
            f"Skipped effects: {', '.join(story.get('skipped_effects', []))}",
            f"Ticker text: {story.get('ticker_text', '')}",
            f"Duration: {story.get('short_duration', '')}",
            f"Effect title: {story.get('effect_title', story.get('title', ''))}",
            f"Zoom value: {float(story.get('zoom_intensity', 1.0)):.2f}x",
            f"Zoom mode: {story.get('zoom_mode', 'center')}",
            f"Focus mode: {story.get('focus_mode', 'center')}",
            f"Focus point: {story.get('motion_focus_point', '')}",
            f"Headline text: {story.get('effect_title', story.get('title', 'LANCE DO JOGO'))}",
            f"CTA text: {story.get('cta_text', 'COMENTA AÍ 👇')}",
            "Output size: 1080x1920",
            "",
        ]),
        encoding="utf-8",
    )
    story["short_metadata_path"] = str(metadata_path)
    logger.info("Saved short metadata: %s", metadata_path)


def create_vertical_short(video_path: str | Path, story: dict[str, Any] | None = None, max_duration_seconds: int = 60) -> str | None:
    """Create a Telegram-friendly vertical 9:16 MP4 short from a downloaded video."""
    source_path = Path(video_path)
    story = story or {}
    try:
        if not source_path.exists() or not source_path.is_file():
            logger.warning("Cannot create vertical short because source video was not found: %s", source_path)
            return None

        shorts_dir = Path("shorts")
        shorts_dir.mkdir(parents=True, exist_ok=True)
        output_path = shorts_dir / f"{source_path.stem}_vertical.mp4"
        duration_limit = max(1, int(story.get("short_duration") or max_duration_seconds or 60))
        explicit_effect_request = "requested_effects" in story
        requested_effects = list(story.get("requested_effects") or (["headline", "cta"] if not explicit_effect_request else []))
        applied_effects: list[str] = []
        foreground_scale = "scale=1080:1920:force_original_aspect_ratio=decrease"
        if "zoom" in requested_effects:
            zoom_intensity = float(story.get("zoom_intensity") or 1.25)
            zoom_mode = str(story.get("zoom_mode") or "smart")
            logger.info("Zoom mode used: %s", zoom_mode)
            logger.info("Zoom requested")
            logger.info("Zoom intensity: %.2fx", zoom_intensity)
            logger.info("Applying visible zoom effect")
            focus = None
            focus_mode = str(story.get("focus_mode") or "action")
            if focus_mode == "manual" or story.get("focus_x") is not None:
                logger.info("Manual focus override enabled")
                focus = (float(story.get("focus_x", 0.5)), float(story.get("focus_y", 0.5)))
            elif focus_mode in {"left", "center", "right", "bottom", "goal_left", "goal_right", "player_left", "player_right"}:
                focus = (float(story.get("focus_x", 0.5)), float(story.get("focus_y", 0.5)))
            elif zoom_mode in {"smart", "smart_strong"}:
                logger.info("Smart focus enabled")
                focus = detect_motion_focus_point(source_path, focus_mode)
            if focus is None:
                logger.info("Fallback to center focus")
                focus = (0.5, 0.5)
            story["motion_focus_point"] = focus
            dimensions = probe_video_dimensions(source_path)
            if dimensions and min(dimensions) < 720 and zoom_intensity >= 1.35:
                logger.warning("Source is low resolution, strong zoom may look blurry")
            frame_count = max(1, duration_limit * 25)
            focus_x, focus_y = focus
            logger.info("Final zoom crop/focus used: mode=%s x=%.3f y=%.3f zoom=%.2fx", focus_mode, focus_x, focus_y, zoom_intensity)
            sharp = str(story.get("quality") or "standard") == "sharp"
            scale_flags = ":flags=lanczos" if sharp else ""
            sharpen = ",unsharp=5:5:0.45:5:5:0" if sharp else ""
            if sharp:
                logger.info("Foreground sharpening enabled")
            logger.info("Background blur enabled")
            logger.info("Output resolution: 1080x1920")
            foreground_scale = (
                f"scale=1080:1920:force_original_aspect_ratio=decrease{scale_flags},"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
                f"zoompan=z='1+{zoom_intensity - 1:.2f}*sin(PI*on/{frame_count})':"
                f"x='max(0,min(iw-iw/zoom,{focus_x:.4f}*iw-iw/zoom/2))':"
                f"y='max(0,min(ih-ih/zoom,{focus_y:.4f}*ih-ih/zoom/2))':"
                f"d=1:s=1080x1920:fps=25{sharpen}"
            )
            applied_effects.append(f"zoom {zoom_intensity:.2f}x")
        filter_complex = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=30:1[bg];"
            f"[0:v]{foreground_scale}[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
        )
        core_filter_complex = filter_complex
        if "freeze" in requested_effects:
            logger.info("Applying freeze effect")
            filter_complex = filter_complex[:-3] + ",tpad=stop_mode=clone:stop_duration=0.7[v]"
            applied_effects.append("freeze")
        if "replay" in requested_effects:
            logger.info("Applying replay effect")
            filter_complex = (
                filter_complex[:-3]
                + "[main];[main]split=2[whole][copy];"
                  "[copy]trim=start=0:end=2,setpts=PTS-STARTPTS[rep];"
                  "[whole][rep]concat=n=2:v=1:a=0[v]"
            )
            applied_effects.append("replay")
        base_filter_complex = filter_complex
        animated_effects: list[str] = []
        static_text_filters: list[str] = []
        animated_text_filters: list[str] = []
        font_path = find_short_font_path()
        if font_path:
            if "headline" in requested_effects:
                logger.info("Applying headline text")
                headline = _shorten_short_text(story.get("effect_title") or story.get("title") or "LANCE DO JOGO")
                static_text_filters.append(_drawtext_filter(headline, "(w-text_w)/2", "80", 82, font_path, "black@0.88", "between(t,0,5)"))
                applied_effects.append("headline")
            if "player_tag" in requested_effects and story.get("player_name"):
                logger.info("Applying player tag")
                static_text_filters.append(_drawtext_filter(str(story["player_name"]), "(w-text_w)/2", "250", 72, font_path, "black@0.85", "between(t,0,5)"))
                applied_effects.append("player_tag")
            if "cta" in requested_effects:
                logger.info("Applying CTA text")
                cta_text = str(story.get("cta_text") or ("COMENTA AÍ 👇" if explicit_effect_request else "COMENTA AI"))
                story["cta_text"] = cta_text
                static_text_filters.append(_drawtext_filter(cta_text, "(w-text_w)/2", "h-text_h-170", 76, font_path, "black@0.88", f"gte(t,{max(0, duration_limit - 4)})"))
                applied_effects.append("cta")
            if "freeze" in requested_effects:
                static_text_filters.append(_drawtext_filter("OLHA ISSO!", "(w-text_w)/2", "(h-text_h)/2", 82, font_path, "black@0.8", f"between(t,{max(0, duration_limit / 2 - .35):.2f},{duration_limit / 2 + .35:.2f})"))
            if "replay" in requested_effects:
                static_text_filters.append(_drawtext_filter("REPLAY", "(w-text_w)/2", "260", 76, font_path, "black@0.8", f"gte(t,{max(0, duration_limit - 2)})"))
            ticker_text = str(story.get("ticker_text") or story.get("effect_title") or "OLHA ESSE LANCE")
            text_position = str(story.get("text_position") or "bottom")
            ticker_y = {"top": "300", "middle": "(h-text_h)/2", "bottom": "h-text_h-330"}.get(text_position, "h-text_h-330")
            speed = {"slow": 90, "medium": 150, "fast": 230}.get(str(story.get("text_speed") or "medium"), 150)
            if "ticker" in requested_effects:
                logger.info("Applying ticker effect")
                animated_text_filters.append(_drawtext_filter(ticker_text, f"w-mod(t*{speed},w+text_w)", ticker_y, 66, font_path, "black@0.78"))
                animated_effects.append("ticker")
            if "slide_text" in requested_effects:
                logger.info("Applying slide text effect")
                slide_x = "if(lt(t,1),w-(w-(w-text_w)/2)*t,if(lt(t,4),(w-text_w)/2,(w-text_w)/2-(t-4)*(w+text_w)))"
                animated_text_filters.append(_drawtext_filter(ticker_text, slide_x, ticker_y, 70, font_path, "black@0.8", "between(t,0,5)"))
                animated_effects.append("slide_text")
            if "impact_words" in requested_effects:
                logger.info("Applying impact words")
                for index, word in enumerate(story.get("impact_words") or ["GOL!", "VAR?", "POLÊMICA!", "OLHA ISSO!"]):
                    animated_text_filters.append(_drawtext_filter(str(word), "(w-text_w)/2", "(h-text_h)/2", 100, font_path, "black@0.82", f"between(t,{index},{index + 1})"))
                animated_effects.append("impact_words")
            if "pulse_text" in requested_effects:
                logger.info("Applying pulse text")
                middle = duration_limit / 2
                animated_text_filters.append(_drawtext_filter(ticker_text, "(w-text_w)/2", "(h-text_h)/2", "86+10*sin(12*t)", font_path, "black@0.82", f"between(t,{max(0, middle - 1):.1f},{middle + 1:.1f})"))
                animated_effects.append("pulse_text")
            if "blink_text" in requested_effects:
                logger.info("Applying blinking text")
                blink_value = str(story.get("blink_text") or story.get("effect_title") or "OLHA ESSE LANCE")
                if story.get("caps", True):
                    blink_value = blink_value.upper()
                blink_y = {"top": "300", "middle": "(h-text_h)/2", "bottom": "h-text_h-330"}.get(str(story.get("blink_position") or "middle"), "(h-text_h)/2")
                blink_start = float(story.get("blink_start", 0) or 0)
                blink_end = float(story.get("blink_end", 5) or 5)
                blink_style = str(story.get("blink_style") or "blink")
                if blink_style == "pulse":
                    fontsize, enable = "88+12*sin(10*t)", f"between(t,{blink_start:g},{blink_end:g})"
                elif blink_style == "impact":
                    fontsize, enable = 108, f"between(t,{blink_start:g},{min(blink_end, blink_start + 1.5):g})"
                else:
                    fontsize, enable = 94, f"between(t,{blink_start:g},{blink_end:g})*lt(mod(t,1),0.55)"
                animated_text_filters.append(_drawtext_filter(blink_value, "(w-text_w)/2", blink_y, fontsize, font_path, "black@0.85", enable))
                animated_effects.append("blink_text")
            static_text_filters.append(_drawtext_filter("Futeba & Juninho", "w-text_w-38", "42", 36, font_path, "black@0.68"))
            applied_effects.append("watermark")
            applied_effects.extend(animated_effects)
            text_filters = ",".join(static_text_filters + animated_text_filters)
            filter_complex = filter_complex[:-3] + f",{text_filters}[v]"
        else:
            logger.warning("No Shorts font found. Creating vertical video without text overlay.")

        command_base = [
            "ffmpeg",
            "-y",
            "-i", str(source_path),
            "-t", str(duration_limit),
        ]
        command_tail = [
            "-map", "[v]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "28",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
        ]
        command = command_base + ["-filter_complex", filter_complex] + command_tail
        story["short_input_file"] = str(source_path)
        story["short_duration"] = duration_limit
        story["applied_effects"] = applied_effects
        logger.info("Final ffmpeg filter: %s", filter_complex)
        logger.info("Creating vertical short for Telegram: %s", source_path)
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=180, **SUBPROCESS_TEXT_KWARGS)
        except subprocess.CalledProcessError as exc:
            if animated_text_filters and font_path:
                logger.warning("Text animation failed, retrying without animated text: %s", str(exc.stderr or exc).strip())
                story["skipped_effects"] = animated_effects
                story["applied_effects"] = [effect for effect in applied_effects if effect not in animated_effects]
                filter_complex = base_filter_complex[:-3] + f",{','.join(static_text_filters)}[v]"
            else:
                logger.warning("ffmpeg text overlay failed. Retrying vertical short without text: %s", str(exc.stderr or exc).strip())
                filter_complex = base_filter_complex
            command = command_base + ["-filter_complex", filter_complex] + command_tail
            try:
                result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=180, **SUBPROCESS_TEXT_KWARGS)
            except subprocess.CalledProcessError as structural_exc:
                structural_effects = [effect for effect in ("freeze", "replay") if effect in requested_effects]
                if not structural_effects:
                    raise
                logger.warning("Structural effects failed; skipping %s and continuing: %s", structural_effects, str(structural_exc.stderr or structural_exc).strip())
                story.setdefault("skipped_effects", []).extend(structural_effects)
                story["applied_effects"] = [effect for effect in story.get("applied_effects", []) if effect not in structural_effects]
                fallback_text = f",{','.join(static_text_filters)}" if static_text_filters and font_path else ""
                filter_complex = core_filter_complex[:-3] + fallback_text + "[v]"
                command = command_base + ["-filter_complex", filter_complex] + command_tail
                result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=180, **SUBPROCESS_TEXT_KWARGS)
        if result.stderr:
            logger.debug("ffmpeg stderr: %s", result.stderr)
        if output_path.exists() and output_path.is_file():
            logger.info("Created vertical short: %s", output_path)
            _write_short_metadata_file(story, output_path)
            story["vertical_short_path"] = str(output_path)
            return str(output_path)
        logger.error("ffmpeg completed but vertical short was not created: %s", output_path)
        return None
    except subprocess.CalledProcessError as exc:
        logger.error("ffmpeg failed to create vertical short: %s", str(exc.stderr or exc).strip())
        return None
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timed out while creating vertical short.")
        return None
    except FileNotFoundError:
        logger.error("ffmpeg command not found. Sending original downloaded video instead.")
        return None
    except Exception as exc:
        logger.error("Unexpected error creating vertical short: %s", exc)
        return None


def probe_video_duration_seconds(video_path: str | Path) -> float | None:
    """Return video duration in seconds using ffprobe."""
    command = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30, **SUBPROCESS_TEXT_KWARGS)
        return float(str(result.stdout or "").strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, ValueError) as exc:
        logger.warning("Could not probe video duration for %s: %s", video_path, exc)
        return None


def detect_scene_change_timestamps(video_path: str | Path, duration_seconds: float, limit: int = 20) -> list[float]:
    """Try to find scene-change timestamps with ffmpeg, returning an empty list on failure."""
    safe_start = 2.0 if duration_seconds > 5 else 0.0
    safe_end = max(safe_start, duration_seconds - 2.0) if duration_seconds > 8 else duration_seconds
    command = [
        "ffmpeg",
        "-hide_banner",
        "-i", str(video_path),
        "-vf", r"select=gt(scene\,0.35),showinfo",
        "-an",
        "-f", "null",
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60, **SUBPROCESS_TEXT_KWARGS)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []

    output = f"{result.stdout}\n{result.stderr}"
    timestamps: list[float] = []
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", output):
        timestamp = float(match.group(1))
        if safe_start <= timestamp <= safe_end:
            timestamps.append(timestamp)
        if len(timestamps) >= limit:
            break
    return timestamps


def detect_audio_peak_timestamps(video_path: str | Path, duration_seconds: float, limit: int = 8) -> list[float]:
    """Detect likely excitement moments from high audio energy using ffmpeg astats."""
    safe_start = 2.0 if duration_seconds > 5 else 0.0
    safe_end = max(safe_start, duration_seconds - 2.0) if duration_seconds > 8 else duration_seconds
    command = [
        "ffmpeg",
        "-hide_banner",
        "-i", str(video_path),
        "-vn",
        "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-f", "null",
        "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=90, **SUBPROCESS_TEXT_KWARGS)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []

    candidates: list[tuple[float, float]] = []
    current_timestamp: float | None = None
    output = f"{result.stdout}\n{result.stderr}"
    for line in output.splitlines():
        timestamp_match = re.search(r"pts_time:([0-9]+(?:\.[0-9]+)?)", line)
        if timestamp_match:
            current_timestamp = float(timestamp_match.group(1))
            continue
        level_match = re.search(r"lavfi\.astats\.Overall\.RMS_level=([-+]?(?:inf|nan|[0-9]+(?:\.[0-9]+)?))", line, flags=re.IGNORECASE)
        if not level_match or current_timestamp is None:
            continue
        level_text = level_match.group(1).lower()
        if level_text in {"inf", "+inf", "-inf", "nan"}:
            continue
        timestamp = current_timestamp
        if safe_start <= timestamp <= safe_end:
            candidates.append((timestamp, float(level_text)))
        current_timestamp = None

    selected: list[float] = []
    for timestamp, _level in sorted(candidates, key=lambda item: item[1], reverse=True):
        if all(abs(timestamp - selected_timestamp) >= 10.0 for selected_timestamp in selected):
            selected.append(round(timestamp, 2))
        if len(selected) >= limit:
            break
    return sorted(selected)


def merge_moment_segments(segments: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping or touching moment windows."""
    if not segments:
        return []
    ordered = sorted(segments)
    merged: list[tuple[float, float]] = [ordered[0]]
    for start, end in ordered[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return [(round(start, 2), round(end, 2)) for start, end in merged]


def clamp_centered_segment(center: float, duration: float, before_seconds: float = 8.0, after_seconds: float = 14.0) -> tuple[float, float]:
    """Create one padded segment around a moment center."""
    safe_start = 2.0 if duration > 24 else 0.0
    safe_end = max(safe_start + 1.0, duration - 2.0) if duration > 8 else duration
    target_length = before_seconds + after_seconds
    start = center - before_seconds
    end = center + after_seconds

    if start < safe_start:
        end += safe_start - start
        start = safe_start
    if end > safe_end:
        start -= end - safe_end
        end = safe_end
    start = max(0.0, max(safe_start, start))
    end = min(duration, max(start + 1.0, end))
    if end - start > target_length:
        end = start + target_length
    return round(start, 2), round(end, 2)


def limit_segments_total_duration(segments: list[tuple[float, float]], max_total_seconds: float) -> list[tuple[float, float]]:
    """Trim segment ends if merged windows exceed the allowed total."""
    limited: list[tuple[float, float]] = []
    remaining = max(1.0, max_total_seconds)
    for start, end in segments:
        length = end - start
        if remaining <= 0:
            break
        if length <= remaining:
            limited.append((start, end))
            remaining -= length
        else:
            limited.append((start, round(start + remaining, 2)))
            break
    return [(round(start, 2), round(end, 2)) for start, end in limited if end > start]


def select_best_moment_segments(duration_seconds: float, moment_timestamps: list[float] | None = None, max_total_seconds: int = 50) -> list[tuple[float, float]]:
    """Select natural football segments around moment centers."""
    duration = max(0.0, float(duration_seconds or 0))
    max_total = max(1.0, float(max_total_seconds or 50))
    if duration <= 0:
        return []

    safe_start = 2.0 if duration > 24 else 0.0
    safe_end = max(safe_start + 1.0, duration - 2.0) if duration > 8 else duration
    centers = [timestamp for timestamp in (moment_timestamps or []) if safe_start <= timestamp <= safe_end]
    if centers:
        target_centers = [duration * 0.35, duration * 0.70]
        selected_centers: list[float] = []
        available_centers = list(centers)
        for target_center in target_centers:
            if not available_centers:
                break
            closest = min(available_centers, key=lambda timestamp: abs(timestamp - target_center))
            selected_centers.append(closest)
            available_centers.remove(closest)
        centers = selected_centers
    else:
        centers = [duration * 0.35, duration * 0.70]

    segments = merge_moment_segments([clamp_centered_segment(center, duration) for center in centers])
    return limit_segments_total_duration(segments, max_total)


def _run_moments_concat_command(source_path: Path, output_path: Path, segments: list[tuple[float, float]]) -> bool:
    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    for index, (start, end) in enumerate(segments):
        filter_parts.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{index}]")
        filter_parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{index}]")
        concat_inputs.append(f"[v{index}][a{index}]")
    filter_complex = ";".join(filter_parts + [f"{''.join(concat_inputs)}concat=n={len(segments)}:v=1:a=1[v][a]"])
    command = [
        "ffmpeg",
        "-y",
        "-i", str(source_path),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "[a]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "30",
        "-c:a", "aac",
        "-b:a", "96k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=180, **SUBPROCESS_TEXT_KWARGS)
    if result.stderr:
        logger.debug("ffmpeg moments stderr: %s", result.stderr)
    return output_path.exists() and output_path.is_file()


def _create_start_moments_clip(source_path: Path, output_path: Path, duration_seconds: float | None, max_total_seconds: int) -> str | None:
    clip_duration = min(35.0, float(max_total_seconds or 45))
    start_offset = 2.0
    if duration_seconds:
        start_offset = max(0.0, duration_seconds * 0.20)
        if duration_seconds > 8:
            start_offset = min(start_offset, max(0.0, duration_seconds - clip_duration - 2.0))
        clip_duration = max(1.0, min(clip_duration, max(1.0, duration_seconds - start_offset)))
    command = [
        "ffmpeg",
        "-y",
        "-ss", str(round(start_offset, 2)),
        "-i", str(source_path),
        "-t", str(round(clip_duration, 2)),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "30",
        "-c:a", "aac",
        "-b:a", "96k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120, **SUBPROCESS_TEXT_KWARGS)
        if result.stderr:
            logger.debug("ffmpeg fallback moments stderr: %s", result.stderr)
        if output_path.exists() and output_path.is_file():
            logger.info("Created fallback best moments clip: %s", output_path)
            return str(output_path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.error("Fallback best moments clip failed for %s: %s", source_path, exc)
    return None


def create_best_moments_clip(video_path: str | Path, story: dict[str, Any] | None = None, max_total_seconds: int = 50) -> str | None:
    """Create a short horizontal best-moments MP4 before vertical conversion."""
    source_path = Path(video_path)
    story = story or {}
    try:
        if not source_path.exists() or not source_path.is_file():
            logger.warning("Cannot create best moments clip because source video was not found: %s", source_path)
            return None

        duration = probe_video_duration_seconds(source_path)
        logger.info("Original TVNZ highlight duration: %s seconds", round(duration, 2) if duration else "unknown")
        max_total = max(1, int(max_total_seconds or 50))
        if duration is not None and duration <= max_total:
            logger.info("Source is already short enough for Telegram moments: %s", source_path)
            story["moments_clip_path"] = str(source_path)
            story["moments_duration_seconds"] = duration
            return str(source_path)

        shorts_dir = Path("shorts")
        shorts_dir.mkdir(parents=True, exist_ok=True)
        output_path = shorts_dir / f"{source_path.stem}_moments.mp4"
        working_duration = duration or float(max_total * 3)
        audio_timestamps = detect_audio_peak_timestamps(source_path, working_duration)
        for timestamp in audio_timestamps[:2]:
            logger.info("Selected audio peak timestamp: %s", timestamp)
        moment_timestamps = audio_timestamps
        if not moment_timestamps:
            moment_timestamps = detect_scene_change_timestamps(source_path, working_duration)
        segments = select_best_moment_segments(working_duration, moment_timestamps, max_total)
        for start, end in segments:
            logger.info("Selected moment segment: start=%s end=%s", start, end)
        try:
            if segments and _run_moments_concat_command(source_path, output_path, segments):
                logger.info("Created best moments clip: %s", output_path)
                story["moments_clip_path"] = str(output_path)
                story["moments_duration_seconds"] = sum(end - start for start, end in segments)
                return str(output_path)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("Best moments concat failed. Falling back to a 35-second clip: %s", exc)

        fallback_path = _create_start_moments_clip(source_path, output_path, duration, max_total)
        if fallback_path:
            story["moments_clip_path"] = fallback_path
            story["moments_duration_seconds"] = min(35.0, float(max_total))
        return fallback_path
    except Exception as exc:
        logger.error("Unexpected error creating best moments clip: %s", exc)
        return None


def send_downloaded_video_to_telegram(file_path: str | Path, story: dict[str, Any]) -> bool:
    """Send a downloaded MP4 to Telegram, falling back safely when upload fails or is too large."""
    explicit_config = story.get("_telegram_config") if isinstance(story.get("_telegram_config"), dict) else None
    telegram_config = explicit_config if explicit_config is not None else load_config()
    dedupe_config = explicit_config or {}
    token = str(telegram_config.get("telegram_bot_token", "") or "")
    chat_id = str(telegram_config.get("telegram_chat_id", "") or "")
    title = str(story.get("title", "") or "").strip()
    source = str(story.get("source") or next(iter(story.get("sources") or []), "Unknown"))
    media_url = str(story.get("video_url") or story.get("link") or Path(file_path).resolve())
    media_id = str(story.get("dedupe_id") or normalize_media_id(media_url))
    state_category = str(story.get("delivery_state_category") or "sent_video_ids")
    state_item_id = str(story.get("processing_state_id") or media_id)
    if not token or not chat_id:
        logger.warning("Telegram credentials are not configured. Skipping downloaded video delivery for %s", title)
        return False
    if not should_send_once(media_id, media_url, title, source, "video", dedupe_config):
        return False

    path = Path(file_path)
    try:
        if not path.exists() or not path.is_file():
            release_send_reservation(dedupe_config, media_id)
            logger.warning("Downloaded video file not found for Telegram delivery: %s", path)
            return False
        file_size = path.stat().st_size
        story["final_video_file_size_bytes"] = file_size
        absolute_path = path.resolve()
        caption = build_downloaded_video_caption(story).replace(token, "[REDACTED]")
        safe_caption = caption.encode("utf-8", errors="replace").decode("utf-8")[:1024]
        logger.info("Telegram output path: %s", absolute_path)
        logger.info("Telegram output size: %.2f MB", file_size / (1024 * 1024))
        logger.info("Telegram video caption: %s", safe_caption)
        logger.info("Telegram destination chat id: %s", chat_id)
        if file_size > TELEGRAM_VIDEO_FILE_LIMIT_BYTES:
            message = (
                "Vídeo baixado no PC, mas muito grande para enviar pelo Telegram.\n"
                f"Caminho local: {path}"
            )
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code != 200:
                logger.error("Telegram %s\nResponse:\n%s", response.status_code, str(response.text or "").replace(token, "[REDACTED]"))
                release_send_reservation(dedupe_config, media_id)
                return False
            story["delivery_failed_after_creation"] = True
            release_send_reservation(dedupe_config, media_id)
            return True

        for method, field_name in (("sendVideo", "video"), ("sendDocument", "document")):
            try:
                with path.open("rb") as video_file:
                    response = requests.post(
                        f"https://api.telegram.org/bot{token}/{method}",
                        data={"chat_id": chat_id, "caption": safe_caption},
                        files={field_name: video_file},
                        timeout=max(REQUEST_TIMEOUT_SECONDS, 120),
                    )
            except requests.RequestException as exc:
                logger.error("Telegram %s request failed: %s", method, exc)
                if method == "sendVideo":
                    logger.info("Telegram sendVideo failed. Retrying with sendDocument.")
                    continue
                break
            try:
                json_method = getattr(response, "json", None)
                response_body = json_method() if callable(json_method) else {"ok": response.status_code == 200}
            except (ValueError, TypeError):
                response_body = {"ok": False, "raw": str(response.text or "")}
            logger.info("Telegram %s HTTP status: %s", method, response.status_code)
            logger.info("Telegram %s response: %s", method, str(response_body).replace(token, "[REDACTED]"))
            if response.status_code == 200 and response_body.get("ok") is True:
                logger.info("Downloaded video sent to Telegram with %s for %s", method, title)
                mark_as_sent(
                    dedupe_config, state_category, state_item_id,
                    source, title, media_url,
                )
                return True
            logger.error("Telegram %s\nResponse:\n%s", response.status_code, str(response.text or "").replace(token, "[REDACTED]"))
            if method == "sendVideo":
                logger.info("Telegram sendVideo failed. Retrying with sendDocument.")
        release_send_reservation(dedupe_config, media_id)
        story["delivery_failed_after_creation"] = True
        _send_telegram_text(
            token, chat_id,
            f"Short foi criado, mas não consegui enviar pelo Telegram. Arquivo local: {absolute_path}",
        )
        return False
    except requests.exceptions.RequestException as exc:
        release_send_reservation(dedupe_config, media_id)
        story["delivery_failed_after_creation"] = True
        logger.error("Telegram downloaded video delivery failed for %s: %s", title, exc)
        _send_telegram_text(
            token, chat_id,
            f"Short foi criado, mas não consegui enviar pelo Telegram. Arquivo local: {path.resolve()}",
        )
        return False
    except Exception as exc:
        release_send_reservation(dedupe_config, media_id)
        story["delivery_failed_after_creation"] = True
        logger.error("Unexpected Telegram downloaded video delivery failure for %s: %s", title, exc)
        return False


def video_platform_from_url(url: str) -> str:
    normalized = str(url or "").casefold()
    if "youtu.be" in normalized or "youtube.com" in normalized:
        return "YouTube"
    if "x.com" in normalized or "twitter.com" in normalized:
        return "X/Twitter"
    if "tiktok.com" in normalized:
        return "TikTok"
    if "instagram.com" in normalized:
        return "Instagram"
    return "Web"


def is_instagram_video_url(url: Any) -> bool:
    """Accept only Instagram reel, post, and TV URLs."""
    return bool(re.search(r"https?://(?:www\.)?instagram\.com/(?:reel|p|tv)/", str(url or ""), re.IGNORECASE))


def load_manual_links(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    path = Path((config or {}).get("manual_links_file", MANUAL_LINKS_FILE))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_manual_links(links: list[dict[str, Any]], config: dict[str, Any] | None = None) -> None:
    path = Path((config or {}).get("manual_links_file", MANUAL_LINKS_FILE))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(links[-50:], ensure_ascii=False, indent=2), encoding="utf-8")


def build_manual_open_payload(video: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    url = str(video.get("url") or video.get("webpage_url") or video.get("video_url") or "").strip()
    title = str(video.get("title") or "Football video").strip()
    source = str(video.get("source") or video.get("channel") or video.get("uploader") or "Unknown").strip()
    platform = str(video.get("platform") or video_platform_from_url(url)).strip()
    status = str(video.get("status") or "manual open required").strip()
    if platform == "Instagram":
        text = (
            "🚨 INSTAGRAM ENCONTRADO\n\n"
            f"Título: {title}\nFonte: Instagram\nStatus: abrir manualmente\n\nLink: {url}"
        )
        button_text = "ABRIR INSTAGRAM"
    else:
        text = (
            f"{MANUAL_OPEN_MESSAGE}\n\n"
            f"Title: {title}\nSource: {source}\nPlatform: {platform}\n"
            f"Original URL: {url}\nStatus: {status}"
        )
        button_text = "ABRIR VÍDEO"
    return {
        "chat_id": str(config.get("telegram_chat_id", "") or ""),
        "text": text,
        "disable_web_page_preview": False,
        "reply_markup": {"inline_keyboard": [[{"text": button_text, "url": url}]]},
    }


def send_manual_open_alert(video: dict[str, Any], config: dict[str, Any]) -> bool:
    """Persist and send a manual-only video link, suppressing duplicate alerts."""
    url = str(video.get("url") or video.get("webpage_url") or video.get("video_url") or "").strip()
    video_id = str(video.get("id") or video.get("video_id") or extract_youtube_video_id(url) or "").strip()
    if not url:
        return False
    media_id = normalize_media_id(url)
    recent = load_manual_links(config)
    if _state_dir(config) is None and any(
        str(item.get("url") or "").strip() == url
        or bool(video_id and str(item.get("video_id") or "").strip() == video_id)
        for item in recent
    ):
        logger.info("Duplicate manual link skipped: %s", url)
        return False
    token = str(config.get("telegram_bot_token", "") or "")
    if not token or not config.get("telegram_chat_id"):
        logger.warning("Telegram credentials are not configured. Manual link not sent: %s", url)
        return False
    source = str(video.get("source") or video.get("channel") or video.get("uploader") or "Unknown")
    title = str(video.get("title") or "Football video")
    if not should_send_once(media_id, url, title, source, "manual_open", config):
        if video_platform_from_url(url) == "Instagram":
            logger.info("Instagram duplicate skipped: %s", url)
        return False
    payload = build_manual_open_payload(video, config)
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            release_send_reservation(config, media_id)
            return False
    except requests.RequestException:
        release_send_reservation(config, media_id)
        return False
    recent.append({
        "title": str(video.get("title") or "Football video"),
        "source": str(video.get("source") or video.get("channel") or video.get("uploader") or "Unknown"),
        "platform": str(video.get("platform") or video_platform_from_url(url)),
        "url": url,
        "video_id": video_id,
        "status": str(video.get("status") or "manual open required"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    save_manual_links(recent, config)
    mark_as_sent(
        config, "manual_open_links", media_id,
        source, title, url,
    )
    logger.info("Manual open link sent: %s", url)
    if video_platform_from_url(url) == "Instagram":
        logger.info("Instagram manual link sent: %s", url)
    return True


def send_controversy_alert(article: dict[str, Any], config: dict[str, Any]) -> bool:
    """Send a high-priority controversy alert with 24-hour title/source deduplication."""
    apply_priority_scores(article)
    if article["controversy_score"] < CONTROVERSY_THRESHOLD:
        return False
    title = str(article.get("title") or "Football controversy").strip()
    sources = article.get("sources") or [article.get("source") or "Unknown"]
    source = str(next((item for item in sources if item), "Unknown"))
    links = article.get("links") or []
    url = str(article.get("link") or article.get("video_url") or (links[0] if links else "")).strip()
    if not url:
        return False
    media_id = normalize_media_id(url)
    now = datetime.now(timezone.utc)
    recent = load_manual_links(config)
    for item in recent if _state_dir(config) is None else []:
        if str(item.get("url") or "") == url:
            logger.info("Duplicate controversy skipped: %s", url)
            return False
        try:
            created = datetime.fromisoformat(str(item.get("created_at") or ""))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if (
            normalize_channel_name(item.get("title")) == normalize_channel_name(title)
            and channel_identity(item.get("source")) == channel_identity(source)
            and (now - created).total_seconds() < 86400
        ):
            logger.info("Duplicate controversy skipped: %s | %s", source, title)
            return False
    token = str(config.get("telegram_bot_token", "") or "")
    chat_id = str(config.get("telegram_chat_id", "") or "")
    if not token or not chat_id:
        return False
    if not should_send_once(media_id, url, title, source, "controversy", config):
        return False
    message = (
        "🚨 POLÊMICA NO FUTEBOL\n\n"
        f"Title: {title}\nSource: {source}\n"
        f"Why it matters: controversy score {article['controversy_score']}/100; priority {article['final_priority_score']}/100\n"
        f"Link: {url}"
    )
    payload = {
        "chat_id": chat_id,
        "text": message,
        "reply_markup": {"inline_keyboard": [[{"text": "ABRIR LANCE", "url": url}]]},
    }
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage", json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            release_send_reservation(config, media_id)
            return False
    except requests.RequestException:
        release_send_reservation(config, media_id)
        return False
    recent.append({
        "kind": "controversy", "title": title, "source": source,
        "platform": video_platform_from_url(url), "url": url,
        "status": "high-priority controversy", "controversy_score": article["controversy_score"],
        "created_at": now.isoformat(),
    })
    save_manual_links(recent, config)
    mark_as_sent(config, "sent_alerts", media_id, source, title, url)
    logger.info("Manual open link sent: %s", url)
    return True


def handle_manual_only_video_source(video: dict[str, Any], config: dict[str, Any]) -> bool:
    """Route X, TikTok, Instagram, and unsupported sources to manual-open only."""
    manual_video = dict(video)
    url = str(manual_video.get("url") or manual_video.get("webpage_url") or "")
    platform = video_platform_from_url(url)
    manual_video["platform"] = platform
    manual_video.setdefault("status", "manual source" if platform in {"X/Twitter", "TikTok", "Instagram"} else "unsupported source")
    if platform == "Instagram" and is_instagram_video_url(url) and config.get("instagram_auto_download") is True:
        return process_instagram_video_source(manual_video, config)
    sent = send_manual_open_alert(manual_video, config)
    if sent and platform in {"X/Twitter", "TikTok", "Instagram"}:
        logger.info("Social manual alert sent: %s | %s", platform, url)
    return sent


def process_instagram_video_source(video: dict[str, Any], config: dict[str, Any]) -> bool:
    """Send the original Instagram link, then optionally process a public download."""
    url = str(video.get("url") or video.get("webpage_url") or "").strip()
    if not is_instagram_video_url(url):
        return False
    manual_video = {
        **video,
        "url": url,
        "platform": "Instagram",
    }
    media_id = normalize_media_id(url)
    manual_sent = send_manual_open_alert(manual_video, config)
    if config.get("instagram_auto_download", True) is not True:
        return manual_sent
    blocked_ttl = float(config.get("blocked_video_retry_ttl_hours", 24) or 24)
    if (
        persistent_state_contains(config, "downloaded_video_ids", media_id)
        or persistent_state_contains(config, "sent_video_ids", media_id)
    ):
        log_duplicate(video.get("source", "Instagram"), video.get("title", ""), url, media_id)
        return manual_sent
    if (
        persistent_state_contains(config, "skipped_geo_blocked", media_id, blocked_ttl)
        or persistent_state_contains(config, "skipped_bot_blocked", media_id, blocked_ttl)
    ):
        log_duplicate(video.get("source", "Instagram"), video.get("title", ""), url, media_id)
        return manual_sent
    if config.get("instagram_use_cookies", False):
        logger.warning("Instagram cookies are not used by this monitor; attempting public extraction only.")
    downloads_dir = Path(config.get("downloads_dir", DOWNLOADS_DIR)) / "instagram"
    yt_dlp_bin = str(config.get("yt_dlp_bin", YT_DLP_BIN))
    command = [
        yt_dlp_bin,
        "--no-playlist",
        "-f", "best[ext=mp4]/best",
        "--paths", str(downloads_dir),
        "--output", "%(title)s-%(id)s.%(ext)s",
        "--print", "after_move:filepath",
        url,
    ]
    try:
        downloads_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Instagram download attempted: %s", url)
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, timeout=120,
            **SUBPROCESS_TEXT_KWARGS,
        )
        stdout_paths = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
        downloaded_path = resolve_downloaded_video_path(url, downloads_dir, stdout_paths)
        if not downloaded_path:
            raise RuntimeError("yt-dlp did not return an existing Instagram file")
        logger.info("Instagram download succeeded: %s", url)
        mark_persistent_state(
            config, "downloaded_video_ids", media_id,
            str(video.get("source") or "Instagram"), str(video.get("title") or ""), url,
        )
        story = {
            "title": video.get("title", "Instagram football video"),
            "source": video.get("source") or video.get("channel") or "Instagram",
            "sources": [video.get("source") or video.get("channel") or "Instagram"],
            "link": url,
            "links": [url],
            "video_url": url,
            "platform": "Instagram",
            "dedupe_id": f"instagram-edit:{media_id.split(':', 1)[-1]}",
            "downloaded_video_path": downloaded_path,
            "_telegram_config": config,
        }
        if process_local_video_file(downloaded_path, config, story):
            return True
        raise RuntimeError("edited Instagram video could not be delivered to Telegram")
    except Exception as exc:
        logger.warning("Instagram download failed: %s (%s)", url, exc)
        error_text = str(getattr(exc, "stderr", "") or exc).casefold()
        if is_geo_restriction_error(error_text):
            mark_persistent_state(config, "skipped_geo_blocked", media_id, "Instagram", str(video.get("title") or ""), url)
        elif any(term in error_text for term in BOT_VERIFICATION_TERMS):
            mark_persistent_state(config, "skipped_bot_blocked", media_id, "Instagram", str(video.get("title") or ""), url)
        return manual_sent


def process_local_video_file(
    file_path: str | Path,
    config: dict[str, Any],
    story: dict[str, Any] | None = None,
) -> bool:
    """Process an existing MP4 through moments, vertical editing, and Telegram."""
    source_path = Path(file_path)
    if not source_path.exists() or not source_path.is_file() or source_path.suffix.casefold() not in {".mp4", ".mov", ".mkv"}:
        logger.error("Local video does not exist or is unsupported: %s", source_path)
        return False
    video_story = dict(story or {})
    video_story.setdefault("title", source_path.stem.replace("_", " ").replace("-", " ").strip() or "Football video")
    video_story.setdefault("source", "Local MP4")
    video_story.setdefault("sources", [video_story["source"]])
    video_story["downloaded_video_path"] = str(source_path)
    video_story["_telegram_config"] = config
    media_url = str(video_story.get("video_url") or video_story.get("link") or source_path.resolve())
    media_id = str(video_story.get("dedupe_id") or normalize_media_id(media_url))
    if persistent_state_contains(config, "sent_video_ids", media_id):
        log_duplicate(video_story.get("source"), video_story.get("title"), media_url, media_id)
        return False
    build_portuguese_shorts_pack(video_story, config)
    moments_path = create_best_moments_clip(source_path, video_story) or str(source_path)
    telegram_path = create_vertical_short(moments_path, video_story) or moments_path
    sent = send_downloaded_video_to_telegram(telegram_path, video_story)
    video_story.pop("_telegram_config", None)
    return sent


def handle_telegram_command(command: str, config: dict[str, Any]) -> bool:
    command = str(command or "").split("@", 1)[0].strip().casefold()
    if command == "/clear_links":
        save_manual_links([], config)
        message = "Recent manual video links cleared."
    elif command in {"/open_links", "/manual_links"}:
        links = load_manual_links(config)
        message = "Recent manual-open video links:\n" + "\n".join(
            f"- {item.get('title', 'Video')} ({item.get('source', 'Unknown')}): {item.get('url', '')}"
            for item in links[-10:]
        ) if links else "No recent manual-open video links."
    elif command == "/controversies":
        links = [item for item in load_manual_links(config) if item.get("kind") == "controversy"]
        message = "Recent controversial links:\n" + "\n".join(
            f"- {item.get('title', 'Controversy')}: {item.get('url', '')}" for item in links[-10:]
        ) if links else "No recent controversial links."
    elif command == "/sources":
        enabled = [source["name"] for source in get_official_video_source_registry(config)]
        if config.get("brazilian_sources_enabled", True):
            enabled.extend(source["name"] for source in BRAZILIAN_SOURCE_REGISTRY)
        if config.get("social_manual_alerts_enabled", True):
            enabled.extend(source["name"] for source in (config.get("manual_social_sources") or DEFAULT_MANUAL_SOCIAL_SOURCES))
        message = "Enabled sources:\n" + "\n".join(f"- {name}" for name in dict.fromkeys(enabled))
    elif command == "/dedupe_status":
        state = load_persistent_state(config) or {category: {} for category in PERSISTENT_STATE_FILES}
        message = (
            "Persistent dedupe status:\n"
            f"- sent alerts: {len(state.get('sent_alerts', {}))}\n"
            f"- sent videos: {len(state.get('sent_video_ids', {}))}\n"
            f"- manual links: {len(state.get('manual_open_links', {}))}\n"
            f"- downloaded videos: {len(state.get('downloaded_video_ids', {}))}\n"
            f"- skipped geo blocked: {len(state.get('skipped_geo_blocked', {}))}\n"
            f"- skipped bot blocked: {len(state.get('skipped_bot_blocked', {}))}"
        )
    elif command == "/clear_dedupe":
        is_local = str(os.getenv("GITHUB_ACTIONS", "")).casefold() != "true"
        if not is_local and not config.get("allow_clear_dedupe", False):
            message = "Dedupe state was not cleared. Set ALLOW_CLEAR_DEDUPE=true to enable this command."
        else:
            state = load_persistent_state(config)
            if state is not None:
                for category in PERSISTENT_STATE_FILES:
                    state[category] = {}
                RUN_DEDUPE_KEYS.clear()
                RESERVED_SEND_IDS.clear()
                save_persistent_state(config)
            message = "Persistent duplicate state cleared."
    else:
        return False
    token = str(config.get("telegram_bot_token", "") or "")
    if not token or not config.get("telegram_chat_id"):
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": config["telegram_chat_id"], "text": message},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


def process_telegram_commands(config: dict[str, Any]) -> None:
    """Process manual-link commands once without blocking monitor execution."""
    token = str(config.get("telegram_bot_token", "") or "")
    if not token:
        return
    endpoint = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        response = requests.get(endpoint, params={"timeout": 0}, timeout=10)
        if response.status_code != 200:
            return
        updates = response.json().get("result", [])
        max_update_id = 0
        for update in updates:
            max_update_id = max(max_update_id, int(update.get("update_id") or 0))
            message = update.get("message", {}) or {}
            if str(message.get("chat", {}).get("id", "")) != str(config.get("telegram_chat_id", "")):
                continue
            handle_telegram_command(str(message.get("text") or ""), config)
        if max_update_id:
            requests.get(endpoint, params={"offset": max_update_id + 1, "timeout": 0}, timeout=10)
    except (requests.RequestException, ValueError, TypeError):
        logger.warning("Telegram command polling failed; monitoring will continue.")


TELEGRAM_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv"}
TELEGRAM_UPDATE_OFFSET_FILE = "telegram_update_offset.json"


def load_telegram_update_offset(config: dict[str, Any]) -> int:
    """Load the next Telegram update id without coupling it to dedupe state."""
    state_dir = _state_dir(config)
    if state_dir is None:
        return 0
    try:
        payload = json.loads((state_dir / TELEGRAM_UPDATE_OFFSET_FILE).read_text(encoding="utf-8"))
        return max(0, int(payload.get("offset", 0)))
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return 0


def save_telegram_update_offset(config: dict[str, Any], offset: int) -> None:
    state_dir = _state_dir(config)
    if state_dir is None:
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / TELEGRAM_UPDATE_OFFSET_FILE).write_text(
        json.dumps({"offset": max(0, int(offset))}, indent=2), encoding="utf-8"
    )
    logger.info("Telegram offset saved: %s", offset)


def _editor_session_key(chat_id: Any, user_id: Any) -> str:
    return f"{chat_id}:{user_id}"


def build_editor_menu_payload(session: dict[str, Any]) -> dict[str, Any]:
    effects = ", ".join(sorted(session.get("effects", []))) or "none"
    text = (
        "🎬 Editor de Short\nEscolha as opções abaixo:\n\n"
        "Current settings:\n"
        f"Duration: {session.get('duration', 10)}s\n"
        f"Zoom: {float(session.get('zoom', 1.35)):.2f}x\n"
        f"Focus: {session.get('focus', 'center')}\n"
        f"Player name: {session.get('player_name') or '-'}\n"
        f"Ticker: {session.get('ticker_text') or '-'}\n"
        f"Effects: {effects}"
    )
    keyboard = [
        [{"text": "🔍 Zoom", "callback_data": "editor:zoom"}, {"text": "🎯 Foco", "callback_data": "editor:focus"}],
        [{"text": "🏷 Nome do jogador", "callback_data": "editor:player"}, {"text": "📝 Texto passando", "callback_data": "editor:ticker"}],
        [{"text": "✨ Texto piscante", "callback_data": "editor:blink"}],
        [{"text": "🧊 Freeze", "callback_data": "editor:toggle:freeze"}, {"text": "🔁 Replay", "callback_data": "editor:toggle:replay"}],
        [{"text": "⏱ Duração", "callback_data": "editor:duration"}],
        [{"text": "✅ Gerar short", "callback_data": "editor:generate"}, {"text": "❌ Cancelar", "callback_data": "editor:cancel"}],
    ]
    return {"text": text, "reply_markup": {"inline_keyboard": keyboard}}


def _send_editor_menu(config: dict[str, Any], chat_id: Any, session: dict[str, Any], message_id: Any = None) -> bool:
    token = str(config.get("telegram_bot_token") or "")
    payload = {"chat_id": chat_id, **build_editor_menu_payload(session)}
    method = "sendMessage"
    if message_id is not None:
        method = "editMessageText"
        payload["message_id"] = message_id
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _editor_choice_keyboard(kind: str) -> list[list[dict[str, str]]]:
    choices = {
        "zoom": [("Sem zoom", "none"), ("Médio 1.20x", "1.20"), ("Forte 1.35x", "1.35"), ("Muito forte 1.50x", "1.50")],
        "focus": [("Centro", "center"), ("Esquerda", "left"), ("Direita", "right"), ("Jogador esquerda", "player_left"), ("Jogador direita", "player_right"), ("Gol esquerda", "goal_left"), ("Gol direita", "goal_right"), ("Manual", "manual")],
        "duration": [("8s", "8"), ("10s", "10"), ("15s", "15"), ("20s", "20"), ("30s", "30")],
    }[kind]
    return [[{"text": label, "callback_data": f"editor:set:{kind}:{value}"}] for label, value in choices]


def handle_editor_text_message(message: dict[str, Any], config: dict[str, Any]) -> bool:
    chat_id = message.get("chat", {}).get("id")
    user_id = message.get("from", {}).get("id")
    session = TELEGRAM_EDITOR_SESSIONS.get(_editor_session_key(chat_id, user_id))
    if not session or not session.get("waiting_for"):
        return False
    value = str(message.get("text") or "").strip()
    waiting = session.pop("waiting_for")
    if waiting == "player_name":
        session["player_name"] = value
        session["effects"].add("player_tag")
        logger.info("Player name saved: %s", value)
    elif waiting == "ticker_text":
        session["ticker_text"] = value
        session["effects"].add("ticker")
        logger.info("Ticker text saved: %s", value)
    elif waiting == "blink_text":
        session["blink_text"] = value
        session["effects"].add("blink_text")
        logger.info("Blink text saved: %s", value)
        keyboard = [
            [{"text": "CAIXA ALTA: ON/OFF", "callback_data": "editor:blink_caps"}],
            [{"text": "Topo", "callback_data": "editor:set:blink_position:top"}, {"text": "Meio", "callback_data": "editor:set:blink_position:middle"}, {"text": "Baixo", "callback_data": "editor:set:blink_position:bottom"}],
            [{"text": "Piscar", "callback_data": "editor:set:blink_style:blink"}, {"text": "Pulsar", "callback_data": "editor:set:blink_style:pulse"}, {"text": "Impacto", "callback_data": "editor:set:blink_style:impact"}],
        ]
        requests.post(f"https://api.telegram.org/bot{config.get('telegram_bot_token')}/sendMessage", json={"chat_id": chat_id, "text": "Configure o texto piscante:", "reply_markup": {"inline_keyboard": keyboard}}, timeout=REQUEST_TIMEOUT_SECONDS)
    elif waiting == "manual_focus":
        try:
            x_text, y_text = value.split(None, 1)
            x, y = float(x_text), float(y_text)
            if not (0 <= x <= 1 and 0 <= y <= 1):
                raise ValueError
            session.update({"focus": "manual", "focus_x": x, "focus_y": y})
            logger.info("Focus saved: %.2f %.2f", x, y)
        except ValueError:
            _send_telegram_text(str(config.get("telegram_bot_token") or ""), chat_id, "Valores inválidos. Exemplo: 0.35 0.70")
            session["waiting_for"] = "manual_focus"
            return True
    _send_editor_menu(config, chat_id, session)
    return True


def handle_editor_callback(callback: dict[str, Any], config: dict[str, Any]) -> bool:
    data = str(callback.get("data") or "")
    if not data.startswith("editor:"):
        return False
    message = callback.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    user_id = callback.get("from", {}).get("id")
    key = _editor_session_key(chat_id, user_id)
    session = TELEGRAM_EDITOR_SESSIONS.get(key)
    token = str(config.get("telegram_bot_token") or "")
    logger.info("Button clicked: %s", data)
    if callback.get("id"):
        try:
            requests.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", json={"callback_query_id": callback["id"]}, timeout=10)
        except requests.RequestException:
            pass
    if not session:
        _send_telegram_text(token, chat_id, "Sessão do editor expirada. Envie o vídeo novamente.")
        return True
    parts = data.split(":")
    action = parts[1]
    if action in {"zoom", "focus", "duration"}:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"Escolha {action}:", "reply_markup": {"inline_keyboard": _editor_choice_keyboard(action)}},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        return True
    if action == "set" and len(parts) >= 4:
        kind, value = parts[2], parts[3]
        if kind == "zoom":
            if value == "none":
                session["effects"].discard("zoom")
                session["zoom"] = 1.0
            else:
                session["effects"].add("zoom")
                session["zoom"] = float(value)
        elif kind == "duration":
            session["duration"] = int(value)
        elif kind == "focus":
            presets = {
                "center": (0.50, 0.60), "left": (0.30, 0.60), "right": (0.70, 0.60),
                "player_left": (0.35, 0.70), "player_right": (0.65, 0.70),
                "goal_left": (0.30, 0.45), "goal_right": (0.70, 0.45),
            }
            if value == "manual":
                session["waiting_for"] = "manual_focus"
                logger.info("Waiting for text input: manual_focus")
                _send_telegram_text(token, chat_id, "Digite focus_x e focus_y. Exemplo: 0.35 0.70")
                return True
            session["focus"] = value
            session["focus_x"], session["focus_y"] = presets[value]
            logger.info("Focus saved: %s", value)
        elif kind == "blink_position":
            session["blink_position"] = value
        elif kind == "blink_style":
            session["blink_style"] = value
        _send_editor_menu(config, chat_id, session)
        return True
    if action in {"player", "ticker", "blink"}:
        waiting = {"player": "player_name", "ticker": "ticker_text", "blink": "blink_text"}[action]
        prompt = {"player": "Digite o nome do jogador:", "ticker": "Digite a frase que vai passar no vídeo:", "blink": "Digite o texto piscante que você quer colocar no vídeo:"}[action]
        session["waiting_for"] = waiting
        logger.info("Waiting for text input: %s", waiting)
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": prompt, "reply_markup": {"force_reply": True}}, timeout=REQUEST_TIMEOUT_SECONDS,
        )
        return True
    if action == "blink_caps":
        session["caps"] = not session.get("caps", True)
        _send_editor_menu(config, chat_id, session)
        return True
    if action == "toggle" and len(parts) >= 3:
        effect = parts[2]
        session["effects"].remove(effect) if effect in session["effects"] else session["effects"].add(effect)
        _send_editor_menu(config, chat_id, session)
        return True
    if action == "cancel":
        TELEGRAM_EDITOR_SESSIONS.pop(key, None)
        logger.info("Session cleared: %s", key)
        _send_telegram_text(token, chat_id, "Editor cancelado.")
        return True
    if action == "generate":
        logger.info("Generate short clicked")
        _send_telegram_text(token, chat_id, "✅ Vou gerar o short agora...")
        focus = session.get("focus", "center")
        story = {
            "title": session.get("title") or session.get("player_name") or "LANCE DO JOGO",
            "source": "Telegram", "sources": ["Telegram"],
            "dedupe_id": f"telegram_editor:{session['file_unique_id']}:{time.time_ns()}",
            "video_url": f"telegram_editor:{session['file_unique_id']}",
            "short_duration": session["duration"], "requested_effects": sorted(session["effects"]),
            "zoom_intensity": session["zoom"], "zoom_mode": "center",
            "focus_mode": focus, "focus_x": session.get("focus_x"), "focus_y": session.get("focus_y"),
            "effect_title": session.get("title") or session.get("player_name") or "LANCE DO JOGO",
            "player_name": session.get("player_name"), "ticker_text": session.get("ticker_text"),
            "blink_text": session.get("blink_text"), "caps": session.get("caps", True),
            "blink_position": session.get("blink_position", "middle"), "blink_style": session.get("blink_style", "blink"),
            "blink_start": session.get("blink_start", 0), "blink_end": session.get("blink_end", 5),
            "cta_text": session.get("cta", "COMENTA AÍ 👇"),
            "telegram_caption": "✅ Short pronto", "_telegram_config": {**config, "telegram_chat_id": chat_id},
        }
        success = process_local_video_file(session["downloaded_file_path"], story["_telegram_config"], story)
        if success:
            TELEGRAM_EDITOR_SESSIONS.pop(key, None)
            logger.info("Session cleared: %s", key)
        return True
    return True


def telegram_video_attachment(message: dict[str, Any]) -> dict[str, Any] | None:
    """Return a supported Telegram video/document attachment."""
    video = message.get("video")
    if isinstance(video, dict) and video.get("file_id"):
        return video
    document = message.get("document")
    if not isinstance(document, dict) or not document.get("file_id"):
        return None
    filename = str(document.get("file_name") or "")
    mime_type = str(document.get("mime_type") or "").casefold()
    if mime_type == "video/mp4" or Path(filename).suffix.casefold() in TELEGRAM_VIDEO_EXTENSIONS:
        return document
    return None


def _send_telegram_text(token: str, chat_id: Any, text: str) -> bool:
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text}, timeout=REQUEST_TIMEOUT_SECONDS,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False


def process_telegram_video_message(message: dict[str, Any], config: dict[str, Any]) -> bool:
    """Download and edit one supported video received by the Telegram bot."""
    attachment = telegram_video_attachment(message)
    if not attachment:
        return False
    token = str(config.get("telegram_bot_token") or "")
    chat_id = message.get("chat", {}).get("id")
    if not token or chat_id is None:
        return False
    unique_id = str(attachment.get("file_unique_id") or attachment.get("file_id") or "")
    raw_caption = str(message.get("caption") or "")
    preview_mode = raw_caption.strip().casefold().startswith("/preview")
    direct_short_mode = raw_caption.strip().casefold().startswith("/short")
    interactive_mode = not preview_mode and not direct_short_mode
    logger.info("Raw Telegram caption: %s", raw_caption)
    short_request = parse_telegram_short_request(raw_caption)
    logger.info("Parsed title: %s", short_request["title"])
    logger.info("Parsed ticker: %s", short_request["ticker_text"])
    logger.info("Parsed force: %s", short_request["force"])
    processing_key = telegram_processing_key(unique_id, short_request)
    logger.info("Telegram processing key: %s", processing_key)
    force_reprocess = bool(short_request.get("force"))
    if force_reprocess:
        logger.info("Force reprocess enabled")
    media_id = processing_key
    title = str(short_request["title"] or attachment.get("file_name") or "Telegram video")
    if not interactive_mode and not preview_mode and not force_reprocess and persistent_state_contains(config, "processed_telegram_shorts", processing_key):
        logger.info("Same options duplicate skipped: %s", processing_key)
        _send_telegram_text(token, chat_id, "Esse vídeo já foi processado com essas mesmas opções.")
        return False
    state = load_persistent_state(config) or {}
    same_video_prefix = f"telegram_short:{unique_id}:"
    if not interactive_mode and not preview_mode and not force_reprocess and any(
        str(key).startswith(same_video_prefix) for key in state.get("processed_telegram_shorts", {})
    ):
        logger.info("Different options detected, processing again")
    if force_reprocess:
        media_id = f"{processing_key}:force:{time.time_ns()}"
    max_bytes = int(float(config.get("telegram_max_download_mb", 100) or 100) * 1024 * 1024)
    if int(attachment.get("file_size") or 0) > max_bytes:
        _send_telegram_text(token, chat_id, "❌ Não consegui cortar esse vídeo. Tente enviar em MP4 menor.")
        return False
    logger.info("Telegram video received: %s", media_id)
    if not interactive_mode:
        _send_telegram_text(token, chat_id, "✅ Vídeo recebido. Vou cortar os melhores momentos agora.")
    try:
        metadata = requests.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": attachment["file_id"]}, timeout=REQUEST_TIMEOUT_SECONDS,
        )
        metadata.raise_for_status()
        remote_path = str(metadata.json()["result"]["file_path"])
        suffix = Path(remote_path).suffix.casefold()
        if suffix not in TELEGRAM_VIDEO_EXTENSIONS:
            suffix = Path(str(attachment.get("file_name") or "video.mp4")).suffix.casefold() or ".mp4"
        target_dir = Path(config.get("downloads_dir", DOWNLOADS_DIR)) / "telegram"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{unique_id}{suffix}"
        download = requests.get(
            f"https://api.telegram.org/file/bot{token}/{remote_path}",
            timeout=max(REQUEST_TIMEOUT_SECONDS, 120),
        )
        download.raise_for_status()
        target.write_bytes(download.content)
        logger.info("Telegram file downloaded: %s", target)
        if interactive_mode:
            user_id = message.get("from", {}).get("id")
            session_key = _editor_session_key(chat_id, user_id)
            TELEGRAM_EDITOR_SESSIONS[session_key] = {
                "file_id": attachment["file_id"], "file_unique_id": unique_id,
                "downloaded_file_path": str(target), "duration": 10,
                "effects": {"zoom", "headline", "watermark"}, "zoom": 1.35,
                "focus": "center", "focus_x": 0.50, "focus_y": 0.60,
                "player_name": "", "ticker_text": "", "title": "", "cta": "COMENTA AÍ 👇",
                "blink_text": "", "caps": True, "blink_position": "middle", "blink_style": "blink", "blink_start": 0, "blink_end": 5,
                "force": False,
            }
            logger.info("Editor session created: %s", session_key)
            return _send_editor_menu(config, chat_id, TELEGRAM_EDITOR_SESSIONS[session_key])
        if preview_mode:
            preview_path = target_dir / f"{unique_id}_focus_preview.jpg"
            font_path = find_short_font_path()
            filters = (
                "scale=1080:1920:force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
                "drawgrid=width=360:height=640:thickness=5:color=white@0.85"
            )
            if font_path:
                labels = [
                    ("left", "80", "260"), ("center", "450", "260"), ("right", "820", "260"),
                    ("player_left", "35", "900"), ("bottom", "455", "900"), ("player_right", "745", "900"),
                ]
                filters += "," + ",".join(_drawtext_filter(label, x, y, 44, font_path, "black@0.75") for label, x, y in labels)
            result = subprocess.run([
                "ffmpeg", "-y", "-i", str(target), "-frames:v", "1", "-vf", filters, str(preview_path),
            ], capture_output=True, text=True, check=True, timeout=60, **SUBPROCESS_TEXT_KWARGS)
            if preview_path.exists():
                with preview_path.open("rb") as preview_file:
                    response = requests.post(
                        f"https://api.telegram.org/bot{token}/sendPhoto",
                        data={"chat_id": chat_id, "caption": "Escolha a zona de foco ou use focus_x/focus_y."},
                        files={"photo": preview_file}, timeout=120,
                    )
                return response.status_code == 200
            return False
        delivery_config = {**config, "telegram_chat_id": chat_id}
        story = {
            "title": title, "source": "Telegram", "sources": ["Telegram"],
            "dedupe_id": media_id, "video_url": media_id,
            "processing_state_id": processing_key,
            "delivery_state_category": "processed_telegram_shorts",
            "short_duration": short_request["duration"],
            "requested_effects": short_request["effects"],
            "zoom_intensity": short_request["zoom_intensity"],
            "zoom_mode": short_request["zoom_mode"],
            "focus_mode": short_request["focus_mode"],
            "focus_x": short_request["focus_x"], "focus_y": short_request["focus_y"],
            "quality": short_request["quality"],
            "effect_title": short_request["title"],
            "cta_text": short_request["cta"],
            "ticker_text": short_request["ticker_text"],
            "impact_words": short_request["impact_words"],
            "text_speed": short_request["text_speed"],
            "text_position": short_request["text_position"],
            "blink_text": short_request["blink_text"], "caps": short_request["caps"],
            "blink_position": short_request["blink_position"], "blink_style": short_request["blink_style"],
            "blink_start": short_request["blink_start"], "blink_end": short_request["blink_end"],
            "telegram_caption": "✅ Short pronto\nEffects applied: " + ", ".join(
                f"zoom {short_request['zoom_intensity']:.2f}x" if effect == "zoom" else effect
                for effect in short_request["effects"]
            ) + (
                f", focus manual x={short_request['focus_x']:.2f} y={short_request['focus_y']:.2f}"
                if short_request["focus_mode"] == "manual" else f", focus {short_request['focus_mode']}"
            ) + ", watermark\nEffects skipped: none",
            "_telegram_config": delivery_config,
        }
        if process_local_video_file(target, delivery_config, story):
            logger.info("Short created: %s", title)
            logger.info("Telegram short sent: %s", title)
            return True
        if story.get("delivery_failed_after_creation"):
            return False
    except (requests.RequestException, KeyError, ValueError, OSError) as exc:
        logger.warning("Telegram video processing failed: %s", exc)
    _send_telegram_text(token, chat_id, "❌ Não consegui cortar esse vídeo. Tente enviar em MP4 menor.")
    return False


def telegram_poll(config: dict[str, Any]) -> None:
    """Long-poll Telegram updates and process commands and incoming videos."""
    token = str(config.get("telegram_bot_token") or "")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is required for --telegram-poll")
        return
    endpoint = f"https://api.telegram.org/bot{token}/getUpdates"
    offset = 0
    while True:
        try:
            response = requests.get(endpoint, params={"offset": offset, "timeout": 30}, timeout=40)
            response.raise_for_status()
            for update in response.json().get("result", []):
                offset = max(offset, int(update.get("update_id") or 0) + 1)
                callback = update.get("callback_query")
                if isinstance(callback, dict):
                    handle_editor_callback(callback, config)
                    continue
                message = update.get("message") or {}
                configured_chat = str(config.get("telegram_chat_id") or "")
                if configured_chat and str(message.get("chat", {}).get("id", "")) != configured_chat:
                    continue
                if handle_editor_text_message(message, config):
                    continue
                if not process_telegram_video_message(message, config):
                    handle_telegram_command(str(message.get("text") or ""), {**config, "telegram_chat_id": message.get("chat", {}).get("id")})
        except (requests.RequestException, ValueError, TypeError):
            logger.warning("Telegram polling failed; retrying shortly.")
            time.sleep(5)


def telegram_poll_once(config: dict[str, Any]) -> None:
    """Fetch and handle the currently pending Telegram updates, then exit."""
    logger.info("Telegram poll once started")
    token = str(config.get("telegram_bot_token") or "")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is required for --telegram-poll-once")
        return
    load_persistent_state(config)
    api = f"https://api.telegram.org/bot{token}"
    try:
        webhook = requests.get(f"{api}/getWebhookInfo", timeout=10)
        webhook_url = str((webhook.json().get("result") or {}).get("url") or "") if webhook.status_code == 200 else ""
        if webhook_url:
            logger.warning("Telegram webhook is set; getUpdates cannot be used while it is active.")
            if config.get("telegram_delete_webhook_on_poll", False):
                deleted = requests.post(f"{api}/deleteWebhook", json={"drop_pending_updates": False}, timeout=10)
                deleted.raise_for_status()
                logger.info("Telegram webhook deleted for polling")
        offset = load_telegram_update_offset(config)
        logger.info("Telegram getUpdates called")
        response = requests.get(f"{api}/getUpdates", params={"offset": offset, "timeout": 0}, timeout=20)
        response.raise_for_status()
        updates = response.json().get("result", [])
        logger.info("Updates found: %s", len(updates))
        if not updates:
            logger.info("No updates found")
            logger.info("No Telegram videos to process")
            save_persistent_state(config)
            logger.info("Telegram poll once finished")
            return
        videos = 0
        for update in sorted(updates, key=lambda item: int(item.get("update_id") or 0)):
            update_id = int(update.get("update_id") or 0)
            try:
                callback = update.get("callback_query")
                if isinstance(callback, dict):
                    handle_editor_callback(callback, config)
                else:
                    message = update.get("message") or {}
                    configured_chat = str(config.get("telegram_chat_id") or "")
                    if configured_chat and str(message.get("chat", {}).get("id", "")) != configured_chat:
                        logger.info("Telegram update intentionally skipped for unconfigured chat: %s", update_id)
                    elif handle_editor_text_message(message, config):
                        pass
                    elif telegram_video_attachment(message):
                        videos += 1
                        logger.info("Processing Telegram video update: %s", update_id)
                        process_telegram_video_message(message, config)
                    else:
                        handle_telegram_command(
                            str(message.get("text") or ""),
                            {**config, "telegram_chat_id": message.get("chat", {}).get("id")},
                        )
                offset = max(offset, update_id + 1)
                save_telegram_update_offset(config, offset)
            except Exception as exc:  # keep a failed update available for the next run
                logger.exception("Unexpected failure processing Telegram update %s: %s", update_id, exc)
                _send_telegram_text(token, config.get("telegram_chat_id"), "❌ Falha ao processar o vídeo. Vou tentar novamente.")
                break
        if not videos:
            logger.info("No Telegram videos to process")
        save_persistent_state(config)
    except Exception as exc:
        logger.exception("Telegram poll once failed: %s", exc)
    logger.info("Telegram poll once finished")


def _parse_positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value or default))
    except (TypeError, ValueError):
        return default


def parse_tvnz_scan_limit(config: dict[str, Any]) -> int:
    """Parse how many recent TVNZ videos to inspect."""
    return _parse_positive_int(
        config.get("tvnz_scan_limit")
        or os.getenv("TVNZ_SCAN_LIMIT")
        or config.get("tvnz_backfill_limit"),
        TVNZ_SCAN_LIMIT_DEFAULT,
    )


def parse_tvnz_max_downloads_per_run(config: dict[str, Any]) -> int:
    """Parse how many new TVNZ highlights may be downloaded in one cycle."""
    return _parse_positive_int(
        config.get("tvnz_max_downloads_per_run")
        or os.getenv("TVNZ_MAX_DOWNLOADS_PER_RUN"),
        TVNZ_MAX_DOWNLOADS_PER_RUN_DEFAULT,
    )


def get_tvnz_youtube_channel_id(config: dict[str, Any]) -> str:
    """Return the configured TVNZ Sport YouTube channel ID."""
    return str(
        config.get("tvnz_youtube_channel_id")
        or os.getenv("TVNZ_YOUTUBE_CHANNEL_ID")
        or TVNZ_YOUTUBE_CHANNEL_ID
    ).strip()


def detect_runner_country() -> str:
    """Best-effort detection of the GitHub runner country, with an env override."""
    override = str(os.getenv("RUNNER_COUNTRY_OVERRIDE", "") or "").strip().upper()
    if override:
        logger.info("Runner country: %s (RUNNER_COUNTRY_OVERRIDE)", override)
        return override
    if str(os.getenv("GITHUB_ACTIONS", "")).casefold() != "true":
        logger.info("Runner country: unknown (not running in GitHub Actions)")
        return ""
    try:
        response = requests.get(RUNNER_LOCATION_URL, headers={"User-Agent": USER_AGENT}, timeout=10)
        response.raise_for_status()
        payload = response.json()
        public_ip = str(payload.get("ip") or "").strip()
        country = str(payload.get("country") or "").strip().upper()
        if public_ip:
            logger.info("Runner public IP: %s", public_ip)
        logger.info("Runner country: %s", country or "unknown")
        return country
    except (requests.RequestException, ValueError, TypeError) as exc:
        logger.warning("Runner country detection failed; continuing without it: %s", exc)
        return ""


def get_official_video_source_registry(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the only channels permitted for regional official-video discovery."""
    return [
        {
            "name": "TVNZ Sport",
            "countries": ["NZ"],
            "channel_id": get_tvnz_youtube_channel_id(config),
        },
        {
            "name": "FIFA",
            "countries": ["GLOBAL", "US"],
            "channel_id": str(
                config.get("fifa_youtube_channel_id")
                or os.getenv("FIFA_YOUTUBE_CHANNEL_ID", "")
                or FIFA_YOUTUBE_CHANNEL_ID
            ).strip(),
        },
    ]


def select_official_video_sources(country: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    country = str(country or "").upper()
    registry = [source for source in get_official_video_source_registry(config) if source.get("channel_id")]
    country_sources = [source for source in registry if country and country in source.get("countries", [])]
    global_sources = [source for source in registry if "GLOBAL" in source.get("countries", [])]
    sources: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for source in country_sources + global_sources:
        identity = channel_identity(source["name"])
        if identity not in seen_names:
            sources.append(source)
            seen_names.add(identity)
    source_names = ", ".join(source["name"] for source in sources) or "none"
    group = "country-specific + GLOBAL" if country_sources and global_sources else "GLOBAL" if global_sources else "none"
    logger.info("Selected official video source group: %s", group)
    logger.info("Selected official sources: %s", source_names)
    return sources


def _tvnz_newest_sort_value(metadata: dict[str, Any], original_index: int) -> tuple[int, int]:
    """Sort newest first when yt-dlp provides dates, otherwise keep page order."""
    for key in ("timestamp", "release_timestamp", "modified_timestamp"):
        try:
            value = int(float(metadata.get(key) or 0))
        except (TypeError, ValueError):
            value = 0
        if value:
            return value, -original_index
    upload_date = re.sub(r"\D", "", str(metadata.get("upload_date") or ""))
    if upload_date:
        try:
            return int(upload_date), -original_index
        except ValueError:
            pass
    return 0, -original_index


def get_tvnz_youtube_channel_url(config: dict[str, Any]) -> str:
    """Return the configured TVNZ Sport YouTube source."""
    configured_url = str(
        config.get("tvnz_youtube_channel_url")
        or os.getenv("TVNZ_YOUTUBE_CHANNEL_URL", "")
        or ""
    ).strip()
    if configured_url:
        return configured_url

    feeds = get_feeds()
    existing_tvnz_source = str(feeds.get(YOUTUBE_DOWNLOAD_CHANNEL, "") or "").strip()
    if existing_tvnz_source:
        return existing_tvnz_source

    return DEFAULT_TVNZ_YOUTUBE_CHANNEL_URL


def _video_url_from_metadata(metadata: dict[str, Any]) -> str:
    video_id = str(metadata.get("id") or metadata.get("video_id") or "").strip()
    video_url = str(metadata.get("webpage_url") or metadata.get("url") or "").strip()
    if video_url and video_url != video_id:
        return video_url
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return video_url


def _rss_entry_value(entry: Any, key: str, default: Any = "") -> Any:
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)


def parse_tvnz_rss_entries(entries: list[Any], scan_limit: int) -> list[dict[str, Any]]:
    """Normalize YouTube RSS entries into TVNZ video metadata."""
    videos: list[dict[str, Any]] = []
    for entry in entries[:scan_limit]:
        video_id = str(
            _rss_entry_value(entry, "yt_videoid")
            or _rss_entry_value(entry, "video_id")
            or ""
        ).strip()
        link = str(_rss_entry_value(entry, "link") or "").strip()
        if not video_id:
            video_id = extract_youtube_video_id(link) or ""
        if not link and video_id:
            link = f"https://www.youtube.com/watch?v={video_id}"
        videos.append({
            "id": video_id,
            "video_id": video_id,
            "title": str(_rss_entry_value(entry, "title") or "").strip(),
            "webpage_url": link,
            "url": link,
            "published": str(_rss_entry_value(entry, "published") or "").strip(),
            "channel": YOUTUBE_DOWNLOAD_CHANNEL,
            "uploader": YOUTUBE_DOWNLOAD_CHANNEL,
        })
    return videos


def discover_tvnz_rss_videos(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Discover TVNZ Sport videos from the official YouTube RSS feed."""
    channel_id = get_tvnz_youtube_channel_id(config)
    rss_url = TVNZ_YOUTUBE_RSS_URL_TEMPLATE.format(channel_id=channel_id)
    scan_limit = parse_tvnz_scan_limit(config)
    logger.info("TVNZ RSS URL: %s", rss_url)
    entries = fetch_feed_entries(rss_url)
    logger.info("TVNZ RSS entries found: %s", len(entries))
    return parse_tvnz_rss_entries(entries, scan_limit)


def _discover_tvnz_sport_videos_with_yt_dlp(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Fallback discovery using yt-dlp flat playlist extraction."""
    yt_dlp_bin = str(config.get("yt_dlp_bin", YT_DLP_BIN))
    source_url = get_tvnz_youtube_channel_url(config)
    scan_limit = parse_tvnz_scan_limit(config)
    logger.info("Discovering recent TVNZ Sport videos from %s (scan_limit=%s)", source_url, scan_limit)
    scanned_videos: list[dict[str, Any]] = []
    urls_to_try = [source_url]
    if source_url.rstrip("/") == DEFAULT_TVNZ_YOUTUBE_CHANNEL_URL.rstrip("/"):
        urls_to_try.append(FALLBACK_TVNZ_YOUTUBE_CHANNEL_URL)

    for attempt_url in urls_to_try:
        command = [
            yt_dlp_bin,
            attempt_url,
            "--dump-json",
            "--flat-playlist",
            "--playlist-end",
            str(scan_limit),
            "--no-warnings",
            "--quiet",
        ]
        logger.info("TVNZ Sport flat playlist command: %s", " ".join(command))
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, timeout=30,
                **SUBPROCESS_TEXT_KWARGS,
            )
        except subprocess.TimeoutExpired:
            logger.warning("TVNZ Sport video discovery timed out for %s.", attempt_url)
            continue
        except FileNotFoundError:
            logger.error("yt-dlp command not found. Cannot discover TVNZ Sport videos.")
            return []

        stdout = result.stdout or ""
        stderr = result.stderr or ""
        logger.info("TVNZ Sport yt-dlp return code: %s", getattr(result, "returncode", 0))
        attempt_videos: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                attempt_videos.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if attempt_videos:
            scanned_videos = attempt_videos
            break

        logger.warning("TVNZ Sport scan returned zero videos; stdout (first 1000 chars): %s", stdout[:1000])
        logger.warning("TVNZ Sport scan returned zero videos; stderr (first 1000 chars): %s", stderr[:1000])
        if attempt_url != urls_to_try[-1]:
            logger.info("TVNZ Sport videos page returned zero videos; trying fallback URL: %s", urls_to_try[-1])

    matched_videos: list[tuple[int, dict[str, Any]]] = []
    for original_index, metadata in enumerate(scanned_videos):
        metadata.setdefault("channel", YOUTUBE_DOWNLOAD_CHANNEL)
        metadata.setdefault("uploader", YOUTUBE_DOWNLOAD_CHANNEL)
        metadata["webpage_url"] = _video_url_from_metadata(metadata)
        if is_tvnz_highlight_video(metadata):
            matched_videos.append((original_index, metadata))
    matched_videos.sort(key=lambda item: _tvnz_newest_sort_value(item[1], item[0]), reverse=True)
    videos = [metadata for _index, metadata in matched_videos]
    logger.info(
        "TVNZ Sport scan summary: scanned=%s matched_highlights=%s",
        len(scanned_videos),
        len(videos),
    )
    return videos


def discover_tvnz_sport_videos(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Discover recent TVNZ Sport videos using RSS, then yt-dlp on failure."""
    try:
        scanned_videos = discover_tvnz_rss_videos(config)
        if not scanned_videos:
            raise ValueError("TVNZ RSS feed returned zero entries")
    except Exception as exc:
        logger.warning("TVNZ RSS discovery failed; falling back to yt-dlp: %s", exc)
        return _discover_tvnz_sport_videos_with_yt_dlp(config)

    matched_videos = [video for video in scanned_videos if is_tvnz_highlight_video(video)]
    logger.info(
        "TVNZ RSS scan summary: entries=%s matched_highlights=%s",
        len(scanned_videos),
        len(matched_videos),
    )
    return matched_videos


OFFICIAL_MATCH_TITLE_TERMS = (
    "match highlights", "fifa world cup", "round of 16", "quarter final",
    "penalties", "shootout",
)
OFFICIAL_TITLE_STOP_WORDS = {
    "match", "highlights", "fifa", "world", "cup", "round", "quarter",
    "final", "penalties", "shootout", "the", "and", "vs", "v",
}


def official_fallback_title_matches(original_title: str, candidate_title: str) -> bool:
    """Match an official fallback using event phrases and team/title terms."""
    if any(_contains_normalized_phrase(candidate_title, term) for term in TVNZ_REJECTED_VIDEO_TERMS):
        return False
    if not any(_contains_normalized_phrase(candidate_title, term) for term in OFFICIAL_MATCH_TITLE_TERMS):
        return False
    original_terms = {
        term for term in re.findall(r"[a-z0-9]+", normalize_channel_name(original_title))
        if len(term) >= 3 and term not in OFFICIAL_TITLE_STOP_WORDS
    }
    candidate_terms = set(re.findall(r"[a-z0-9]+", normalize_channel_name(candidate_title)))
    return not original_terms or len(original_terms & candidate_terms) >= min(2, len(original_terms))


def discover_official_fallback_video(
    title: str,
    country: str,
    config: dict[str, Any],
    exclude_sources: set[str] | None = None,
) -> dict[str, Any] | None:
    """Find the same highlight only in region-eligible whitelisted channel feeds."""
    excluded = {channel_identity(source) for source in (exclude_sources or set())}
    scan_limit = parse_tvnz_scan_limit(config)
    for source in select_official_video_sources(country, config):
        if channel_identity(source["name"]) in excluded:
            continue
        feed_url = build_youtube_feed_url(str(source["channel_id"]))
        logger.info("Discovering official fallback from %s RSS: %s", source["name"], feed_url)
        if feed_url in OFFICIAL_RSS_ENTRIES_CACHE:
            entries = OFFICIAL_RSS_ENTRIES_CACHE[feed_url]
            logger.info("Official RSS cache hit: %s", source["name"])
        else:
            try:
                entries = fetch_feed_entries(feed_url)
            except Exception as exc:
                logger.warning("Official fallback feed failed for %s: %s", source["name"], exc)
                continue
            OFFICIAL_RSS_ENTRIES_CACHE[feed_url] = entries
            logger.info("Official RSS fetched: %s", source["name"])
        for video in parse_tvnz_rss_entries(entries, scan_limit):
            video["channel"] = source["name"]
            video["uploader"] = source["name"]
            if official_fallback_title_matches(title, str(video.get("title") or "")):
                logger.info("Official regional fallback found: %s | %s", source["name"], video.get("webpage_url", ""))
                return video
    return None


def clear_official_rss_cache() -> None:
    """Reset per-process official RSS entries, primarily at run startup."""
    OFFICIAL_RSS_ENTRIES_CACHE.clear()


def send_official_source_unavailable_alert(config: dict[str, Any]) -> bool:
    """Notify Telegram that no whitelisted official source works in this region."""
    token = str(config.get("telegram_bot_token", "") or "")
    chat_id = str(config.get("telegram_chat_id", "") or "")
    if not token or not chat_id:
        logger.warning(OFFICIAL_SOURCE_UNAVAILABLE_MESSAGE)
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": OFFICIAL_SOURCE_UNAVAILABLE_MESSAGE},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        return response.status_code == 200
    except requests.RequestException:
        logger.warning("Failed to send unavailable official source alert to Telegram.")
        return False


def downloaded_tvnz_video_keys(alerts: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """Extract downloaded TVNZ video IDs and URLs from persisted alerts."""
    video_ids = {str(alert.get("video_id", "")) for alert in alerts if alert.get("video_id")}
    video_urls = {str(alert.get("video_url", "")) for alert in alerts if alert.get("video_url")}
    for alert in alerts:
        for link in alert.get("links", []) or []:
            if link:
                video_urls.add(str(link))
    return video_ids, video_urls


def download_new_tvnz_sport_highlights(config: dict[str, Any], alerts: list[dict[str, Any]]) -> int:
    """Download, convert, and send every new matching TVNZ Sport highlight."""
    downloaded_ids, downloaded_urls = downloaded_tvnz_video_keys(alerts)
    downloads_dir = Path(config.get("downloads_dir", DOWNLOADS_DIR))
    yt_dlp_bin = str(config.get("yt_dlp_bin", YT_DLP_BIN))
    max_downloads = parse_tvnz_max_downloads_per_run(config)
    downloaded_count = 0
    matched_videos = discover_tvnz_sport_videos(config)
    selected_videos: list[tuple[dict[str, Any], str, str]] = []
    already_processed_count = 0
    queued_ids = set(downloaded_ids)
    queued_urls = set(downloaded_urls)
    runner_country = detect_runner_country()
    if not runner_country and str(os.getenv("GITHUB_ACTIONS", "")).casefold() != "true":
        runner_country = "NZ"  # Preserve the known-working local-PC behavior.
    available_sources = select_official_video_sources(runner_country, config)
    tvnz_available = any(channel_identity(source["name"]) == channel_identity(YOUTUBE_DOWNLOAD_CHANNEL) for source in available_sources)

    for video in matched_videos:
        video_url = _video_url_from_metadata(video)
        video_id = str(video.get("id") or extract_youtube_video_id(video_url) or "").strip()
        media_id = normalize_media_id(video_url)
        blocked_ttl = float(config.get("blocked_video_retry_ttl_hours", 24) or 24)
        persistent_duplicate = (
            persistent_state_contains(config, "downloaded_video_ids", media_id)
            or persistent_state_contains(config, "sent_video_ids", media_id)
            or persistent_state_contains(config, "skipped_geo_blocked", media_id, blocked_ttl)
            or persistent_state_contains(config, "skipped_bot_blocked", media_id, blocked_ttl)
        )
        if persistent_duplicate:
            already_processed_count += 1
            log_duplicate(YOUTUBE_DOWNLOAD_CHANNEL, video.get("title"), video_url, media_id)
            continue
        duplicate_by_id = bool(video_id and video_id in queued_ids)
        duplicate_by_url = bool(video_url and video_url in queued_urls)
        if duplicate_by_id or duplicate_by_url:
            already_processed_count += 1
            continue
        if len(selected_videos) >= max_downloads:
            continue
        selected_videos.append((video, video_url, video_id))
        if video_id:
            queued_ids.add(video_id)
        if video_url:
            queued_urls.add(video_url)

    logger.info(
        "TVNZ Sport download plan: matched=%s already_processed=%s selected_for_download=%s max_per_run=%s",
        len(matched_videos),
        already_processed_count,
        len(selected_videos),
        max_downloads,
    )
    for video, video_url, _video_id in selected_videos:
        logger.info("Selected TVNZ Sport video: %s | %s", video.get("title", ""), video_url)

    for video, video_url, video_id in selected_videos:
        download_video = video
        download_url = video_url
        if not tvnz_available:
            fallback_video = discover_official_fallback_video(
                str(video.get("title") or ""), runner_country, config, {YOUTUBE_DOWNLOAD_CHANNEL},
            )
            if not fallback_video:
                send_manual_open_alert({
                    **video, "url": video_url, "status": "unsupported source for runner country",
                }, config)
                continue
            download_video = fallback_video
            download_url = _video_url_from_metadata(fallback_video)
        try:
            downloaded_path = download_youtube_video(download_url, downloads_dir, yt_dlp_bin)
        except GeoRestrictedVideoError:
            logger.warning("Official video download is geo-restricted: %s", download_url)
            mark_persistent_state(
                config, "skipped_geo_blocked", normalize_media_id(download_url),
                str(download_video.get("channel") or YOUTUBE_DOWNLOAD_CHANNEL),
                str(download_video.get("title") or ""), download_url,
            )
            send_manual_open_alert({
                **download_video, "url": download_url, "status": "geo-blocked",
            }, config)
            continue
        except VideoDownloadBlockedError:
            logger.warning("Official video download requires bot verification: %s", download_url)
            mark_persistent_state(
                config, "skipped_bot_blocked", normalize_media_id(download_url),
                str(download_video.get("channel") or YOUTUBE_DOWNLOAD_CHANNEL),
                str(download_video.get("title") or ""), download_url,
            )
            send_manual_open_alert({
                **download_video, "url": download_url, "status": "YouTube bot verification",
            }, config)
            continue
        if not downloaded_path:
            send_manual_open_alert({
                **download_video, "url": download_url, "status": "download failed",
            }, config)
            continue
        mark_persistent_state(
            config, "downloaded_video_ids", normalize_media_id(download_url),
            str(download_video.get("channel") or YOUTUBE_DOWNLOAD_CHANNEL),
            str(download_video.get("title") or ""), download_url,
        )

        story = {
            "title": video.get("title", ""),
            "sources": [str(download_video.get("channel") or YOUTUBE_DOWNLOAD_CHANNEL)],
            "source": str(download_video.get("channel") or YOUTUBE_DOWNLOAD_CHANNEL),
            "official_source": str(download_video.get("channel") or YOUTUBE_DOWNLOAD_CHANNEL),
            "link": download_url,
            "links": [download_url],
            "video_url": download_url,
            "video_id": video_id,
            "content_category": "MATCH_HIGHLIGHT",
            "downloaded_video_path": downloaded_path,
            "_telegram_config": config,
        }
        moments_clip_path = create_best_moments_clip(downloaded_path, story) or downloaded_path
        vertical_input_path = moments_clip_path
        telegram_video_path = create_vertical_short(vertical_input_path, story) or moments_clip_path
        if telegram_video_path != vertical_input_path and not story.get("vertical_short_path"):
            story["vertical_short_path"] = str(telegram_video_path)
        story["final_video_duration_seconds"] = story.get("moments_duration_seconds")
        telegram_path = Path(telegram_video_path)
        if telegram_path.exists():
            final_size = telegram_path.stat().st_size
            story["final_video_file_size_bytes"] = final_size
            logger.info("Final Telegram video file size: %s bytes", final_size)
        telegram_sent = send_downloaded_video_to_telegram(telegram_video_path, story)
        story.pop("_telegram_config", None)
        if not telegram_sent:
            logger.warning("Telegram delivery failed for TVNZ Sport video; leaving unprocessed for retry: %s", video_url)
            continue

        alerts.append({
            "alert_type": "tvnz_download",
            "title": story.get("title", ""),
            "sources": [YOUTUBE_DOWNLOAD_CHANNEL],
            "links": [download_url],
            "video_url": download_url,
            "video_id": video_id,
            "downloaded_video_path": downloaded_path,
            "moments_clip_path": story.get("moments_clip_path", ""),
            "vertical_short_path": story.get("vertical_short_path", ""),
        })
        if video_id:
            downloaded_ids.add(video_id)
        if video_url:
            downloaded_urls.add(video_url)
        downloaded_count += 1
        logger.info("Downloaded TVNZ Sport highlight: %s", story.get("title", ""))

    return downloaded_count


def process_cycle(config: dict[str, str]) -> int:
    """Fetch feeds, filter relevant stories, group duplicates, score them, and notify on high viral potential."""
    debug_mode = bool(config.get("debug_mode", False))
    state_file = Path(config.get("state_file", STATE_FILE))
    alerts_file = Path(config.get("alerts_file", ALERTS_FILE))
    seen_articles = load_seen_articles(state_file)
    alerts = load_alerts(alerts_file)
    seen_alert_keys = {alert.get("alert_key", "") for alert in alerts if alert.get("alert_key")}
    seen_this_cycle: set[str] = set()
    relevant_articles: list[dict[str, str]] = []
    persisted_seen_keys: set[str] = set()

    # New: Track notified stories for deduplication and limit
    notified_stories_in_cycle: list[dict[str, Any]] = []

    logger.info("Starting monitoring cycle")
    warned_about_feeds: set[str] = set()
    sources_checked = 0
    videos_found = 0
    articles_found = 0
    stories_rejected = 0
    videos_downloaded = 0
    today_matches = load_todays_fixtures(config)
    if today_matches:
        logger.info("Loaded today's matches: %s", len(today_matches))
    else:
        logger.info("No fixtures loaded.")

    for source, feed_url in get_feeds().items():
        sources_checked += 1
        fetch_status = "ok"
        items_found = 0

        if debug_mode:
            print("==========================")
            print("SOURCE")
            print("==========================")
            print(f"SOURCE NAME: {source}")
            print(f"SOURCE URL: {feed_url}")

        try:
            entries = fetch_feed_entries(feed_url)
            items_found = len(entries)
        except requests.RequestException as exc:
            fetch_status = "error"
            status_code = getattr(exc.response, "status_code", None)
            if status_code == 404 and source not in warned_about_feeds:
                logger.warning("Feed %s returned 404 and will be skipped for this cycle: %s", source, exc)
                warned_about_feeds.add(source)
            elif source not in warned_about_feeds:
                logger.warning("Could not fetch %s feed: %s", source, exc)
                warned_about_feeds.add(source)
            if debug_mode:
                print(f"FETCH STATUS: {fetch_status} ({exc})")
                print(f"NUMBER OF ITEMS FOUND: {items_found}")
            continue
        except Exception as exc:  # pragma: no cover - defensive guard for feedparser issues
            fetch_status = "error"
            logger.warning("Could not parse %s feed: %s", source, exc)
            if debug_mode:
                print(f"FETCH STATUS: {fetch_status} ({exc})")
                print(f"NUMBER OF ITEMS FOUND: {items_found}")
            continue

        if debug_mode:
            print(f"FETCH STATUS: {fetch_status}")
            print(f"NUMBER OF ITEMS FOUND: {items_found}")

        latest_titles: list[str] = []
        matching_count = 0
        duplicate_count = 0

        for entry in entries:
            article = normalize_entry(entry, source)
            attach_article_to_match(article, today_matches)
            latest_titles.append(article.get("title", ""))
            content_decision = classify_story_content(article.get("title", ""), source, article.get("match"))
            article["content_decision"] = content_decision
            article["content_category"] = content_decision["category"]
            article["should_alert"] = content_decision["should_alert"]
            article["should_download"] = content_decision["should_download"]
            logger.info("Content category: %s", content_decision["category"])
            logger.info("Should alert: %s", content_decision["should_alert"])
            logger.info("Should download: %s", content_decision["should_download"])
            logger.info("Decision reason: %s", content_decision["reason"])
            if is_cazetv_discussion_content(source, article.get("title", "")):
                logger.info("Skipping CazéTV discussion content.")
                continue
            if article.get("video_url") and is_youtube_link(article.get("video_url")):
                videos_found += 1

            if not is_relevant_article(article):
                continue

            matched_keywords = [keyword for keyword in KEYWORDS if keyword.lower() in normalize_text(article.get("title", ""))]
            if matched_keywords:
                matching_count += 1

            article_key = build_article_key(article, source)
            duplicate_key = article_key
            if article.get("video_id"):
                duplicate_key = f"{source}:{article.get('video_id')}"
            if not debug_mode:
                if duplicate_key in seen_articles or duplicate_key in seen_this_cycle:
                    duplicate_count += 1
                    continue
                seen_this_cycle.add(duplicate_key)
            article["duplicate_key"] = duplicate_key
            relevant_articles.append(article)
            articles_found += 1
            if debug_mode:
                evaluation_article = {
                    "title": article.get("title", ""),
                    "summary": article.get("summary", "") or article.get("description", ""),
                    "sources": [source],
                    "links": [article.get("link", "")],
                    "video_links": [article.get("video_url", "")] if article.get("video_url") else [],
                    "video_url": article.get("video_url", ""),
                }
                evaluation_article["viral_score"] = calculate_viral_score(evaluation_article)
                accepted = should_send_notification(evaluation_article)
                reason = get_debug_acceptance_reason(article.get("title", ""), [source], evaluation_article["viral_score"])
                print("TITLE")
                print(article.get("title", ""))
                print("SOURCE")
                print(source)
                print("PUBLISHED TIME")
                print(article.get("published", ""))
                print("VIDEO URL")
                print(article.get("video_url", ""))
                print("ARTICLE URL")
                print(article.get("link", ""))
                print("MATCHED KEYWORDS")
                print(", ".join(matched_keywords) if matched_keywords else "None")
                print("VIRAL SCORE")
                print(evaluation_article["viral_score"])
                print("ACCEPTED OR REJECTED")
                print("accepted" if accepted else "rejected")
                print("REASON")
                print(reason)

            logger.info("Detected new relevant article from %s: %s", source, article.get("title"))

        logger.info(
            "Source debug | source=%s | url=%s | items=%s | latest_titles=%s | matched_keywords=%s | duplicates_skipped=%s",
            source,
            feed_url,
            len(entries),
            latest_titles[:5],
            matching_count,
            duplicate_count,
        )

    grouped_articles = group_articles(relevant_articles)
    high_potential_articles: list[dict[str, Any]] = []

    for grouped_article in grouped_articles:
        scored_article = score_article_with_ai(grouped_article, config)
        apply_priority_scores(scored_article)
        if scored_article["controversy_score"]:
            logger.info("Controversy detected: %s", scored_article.get("title", ""))
            logger.info("Controversy score: %s", scored_article["controversy_score"])
        if any(is_brazilian_sports_source(source) for source in scored_article.get("sources", [])):
            logger.info("Brazilian source matched: %s", ", ".join(scored_article.get("sources", [])))
        content_decision = classify_story_content(
            scored_article.get("title", ""),
            scored_article.get("official_source", "") or next(iter(scored_article.get("sources", [])), ""),
            scored_article.get("match"),
        )
        scored_article["content_decision"] = content_decision
        scored_article["content_category"] = content_decision["category"]
        scored_article["should_alert"] = content_decision["should_alert"]
        scored_article["should_download"] = content_decision["should_download"]
        logger.info("Content category: %s", content_decision["category"])
        logger.info("Should alert: %s", content_decision["should_alert"])
        logger.info("Should download: %s", content_decision["should_download"])
        logger.info("Decision reason: %s", content_decision["reason"])
        accepted = content_decision["should_alert"] and (
            scored_article["controversy_score"] >= CONTROVERSY_THRESHOLD
            or should_send_notification(scored_article)
            or is_live_goal_event(scored_article)
        )
        if accepted:
            high_potential_articles.append(scored_article)
            logger.info("High viral potential story: %s (score=%s)", scored_article.get("title"), scored_article.get("score"))
        else:
            stories_rejected += 1
        if debug_mode:
            print("--------------------------")
            print("STORY")
            print("--------------------------")
            print("TITLE")
            print(scored_article.get("title", ""))
            print("SOURCE")
            print(", ".join(scored_article.get("sources", [])))
            print("PUBLISHED TIME")
            print(grouped_article.get("published", ""))
            print("VIDEO URL")
            print(scored_article.get("video_url", ""))
            print("ARTICLE URL")
            print(scored_article.get("links", [""])[0])
            print("MATCHED KEYWORDS")
            print(", ".join([keyword for keyword in KEYWORDS if keyword.lower() in normalize_text(scored_article.get('title', ''))]) if [keyword for keyword in KEYWORDS if keyword.lower() in normalize_text(scored_article.get('title', ''))] else 'None')
            print("VIRAL SCORE")
            print(scored_article.get("viral_score", calculate_viral_score(scored_article)))
            print("ACCEPTED OR REJECTED")
            print("accepted" if accepted else "rejected")
            print("REASON")
            print("accepted" if accepted else "rejected")
    # New: Sort articles to prioritize video content
    high_potential_articles.sort(key=lambda x: 1 if (x.get("video_url") or x.get("video_id")) else 0, reverse=True)

    for article in high_potential_articles:
        alert_title = article.get("title", "").strip()
        alert_score = article.get("score", 0)
        alert_key = f"{alert_title}::{alert_score}"
        if alert_key in seen_alert_keys:
            logger.info("Skipping duplicate alert for %s", article.get("title"))
            continue

        # New: Deduplicate similar stories within the same cycle based on player/team/event and time
        should_notify = True
        for notified_story in notified_stories_in_cycle:
            # Simple heuristic for similarity: same main player/team and similar event terms
            # within a short time frame (e.g., 6 hours, assuming rapid updates for same event)
            title_current = normalize_text(article.get("title", ""))
            title_notified = normalize_text(notified_story.get("title", ""))

            # Very basic keyword overlap check for deduplication
            current_keywords = {k.lower() for k in KEYWORDS if k.lower() in title_current}
            notified_keywords = {k.lower() for k in KEYWORDS if k.lower() in title_notified}

            if current_keywords.intersection(notified_keywords):
                logger.info("Skipping similar story %s (already notified about %s)", article.get("title"), notified_story.get("title"))
                should_notify = False
                break

        if not should_notify:
            continue

        # New: Limit notifications per cycle
        if len(notified_stories_in_cycle) >= 3:
            logger.info("Reached maximum number of notifications for this cycle (3). Skipping %s", article.get("title"))
            continue

        if not any(channel_identity(source) == channel_identity(YOUTUBE_DOWNLOAD_CHANNEL) for source in article.get("sources", [])):
            article["automatic_video_status"] = "Vídeo automático: aguardando TVNZ Sport"

        if int(article.get("controversy_score") or 0) >= CONTROVERSY_THRESHOLD and config.get("monitor_controversies", True):
            notification_sent = send_controversy_alert(article, config)
        elif any(is_brazilian_sports_source(source) for source in article.get("sources", [])):
            article_url = str(article.get("link") or next(iter(article.get("links", [])), ""))
            notification_sent = send_manual_open_alert({
                "title": article.get("title", ""),
                "source": next((source for source in article.get("sources", []) if is_brazilian_sports_source(source)), "Brazilian sports source"),
                "url": article_url,
                "status": "manual open alert",
            }, config)
        else:
            notification_sent = send_telegram_notification(article, config)
        if notification_sent:
            try:
                for x_post in discover_x_posts(article, config)[:3]:
                    handle_manual_only_video_source({
                        "title": x_post.get("text", "X football video"),
                        "source": x_post.get("account_name", "X/Twitter"),
                        "url": x_post.get("url", ""),
                        "status": "manual source",
                    }, config)
            except Exception:
                logger.warning("X discovery failed unexpectedly; monitoring will continue.")
            if not debug_mode:
                for duplicate_key in article.get("duplicate_keys", []):
                    if duplicate_key:
                        persisted_seen_keys.add(str(duplicate_key))

            alerts.append({
                "alert_key": alert_key,
                "title": article.get("title", ""),
                "score": article.get("score", 0),
                "shorts_title": article.get("shorts_title", ""),
                "thumbnail_text": article.get("thumbnail_text", []),
                "thumbnail_frame_idea": article.get("thumbnail_frame_idea", ""),
                "thumbnail_expression": article.get("thumbnail_expression", ""),
                "thumbnail_background": article.get("thumbnail_background", ""),
                "narration_scripts": article.get("narration_scripts", {}),
                "heygen_narration": article.get("heygen_narration", ""),
                "description": article.get("description", ""),
                "hashtags": article.get("hashtags", []),
                "search_keywords": article.get("search_keywords", []),
                "viral_reason": article.get("viral_reason", ""),
                "video_url": article.get("video_url", ""),
                "video_id": article.get("video_id", ""),  # Store video_id for duplicate checking
                "video_search_links": article.get("video_search_links", []),
                "links": article.get("links", []),
                "sources": article.get("sources", []),
                "downloaded_video_path": article.get("downloaded_video_path", ""),
                "automatic_video_status": article.get("automatic_video_status", ""),
            })
            seen_alert_keys.add(alert_key)
            notified_stories_in_cycle.append(article) # Add to notified list for deduplication in current cycle

    if bool(config.get("tvnz_auto_download_enabled", False)):
        videos_downloaded += download_new_tvnz_sport_highlights(config, alerts)

    if not debug_mode:
        updated_seen = seen_articles | persisted_seen_keys
        save_seen_articles(state_file, updated_seen)
    save_alerts(alerts_file, alerts)

    if debug_mode:
        print("==========================")
        print("DEBUG SUMMARY")
        print("==========================")
        print(f"SOURCES CHECKED: {sources_checked}")
        print(f"VIDEOS FOUND: {videos_found}")
        print(f"ARTICLES FOUND: {articles_found}")
        print(f"STORIES MERGED: {len(grouped_articles)}")
        print(f"STORIES REJECTED: {stories_rejected}")
        print(f"VIDEOS DOWNLOADED: {videos_downloaded}") # New debug info
        print(f"NOTIFICATIONS SENT: {len(notified_stories_in_cycle)}") # New debug info
        print(f"FINAL HIGH POTENTIAL STORIES: {len(high_potential_articles)}")
        print(f"Sources checked: {sources_checked}")
        print(f"Videos found: {videos_found}")
        print(f"Articles found: {articles_found}")
        print(f"Stories merged: {len(grouped_articles)}")
        print(f"Stories rejected: {stories_rejected}")
        print(f"Videos downloaded: {videos_downloaded}") # New debug info
        print(f"Notifications sent: {len(notified_stories_in_cycle)}") # New debug info
        print(f"Final high-potential stories: {len(high_potential_articles)}")
        print(f"notícias verificadas: {sources_checked}")
        print(f"vídeos encontrados: {videos_found}")
        print(f"artigos encontrados: {articles_found}")
        print(f"histórias mescladas: {len(grouped_articles)}")
        print(f"histórias rejeitadas: {stories_rejected}")
        print(f"vídeos baixados: {videos_downloaded}") # New debug info
        print(f"notificações enviadas: {len(notified_stories_in_cycle)}") # New debug info
        print(f"histórias finais de alto potencial: {len(high_potential_articles)}")

        ranked_stories = sorted(
            high_potential_articles + [article for article in grouped_articles if article not in high_potential_articles],
            key=lambda article: float(article.get("viral_score", calculate_viral_score(article)) or 0),
            reverse=True,
        )[:10]
        print("\nTop 10 melhores oportunidades")
        for index, story in enumerate(ranked_stories, start=1):
            title = str(story.get("title", "")).strip() or "Sem título"
            score = story.get("viral_score", calculate_viral_score(story))
            line = f"{index}️⃣ {title} — Viral Score: {score}"
            try:
                print(line)
            except UnicodeEncodeError:
                print(f"{index}. {title} - Viral Score: {score}")

    logger.info("Monitoring cycle finished. High-potential stories: %s", len(high_potential_articles))
    return len(high_potential_articles)

def resolve_downloaded_video_path(video_url: str, output_path: Path, stdout_paths: list[str]) -> str | None:
    """Resolve a yt-dlp download path without trusting corrupted stdout paths."""
    downloads_dir = Path(output_path)
    video_id = extract_youtube_video_id(video_url)
    if video_id:
        matches = [
            path for path in downloads_dir.glob(f"*{video_id}*.mp4")
            if path.exists() and path.is_file()
        ]
        if matches:
            resolved_path = max(matches, key=lambda path: path.stat().st_mtime)
            logger.info("Resolved downloaded file by video id: %s", resolved_path)
            return str(resolved_path)

    for path_text in reversed(stdout_paths):
        stdout_path = Path(path_text)
        if stdout_path.exists() and stdout_path.is_file():
            return str(stdout_path)
    return None


def download_youtube_video(video_url: str, output_path: Path, yt_dlp_bin: str = YT_DLP_BIN) -> str | None:
    """Downloads a YouTube video using yt-dlp.

    Args:
        video_url: The URL of the YouTube video to download.
        output_path: The directory where the video should be saved.

    Returns:
        The path to the downloaded video file, or None if the download failed.
    """
    try:
        output_path.mkdir(parents=True, exist_ok=True)
        command = [
            yt_dlp_bin,
            "-f", "best[ext=mp4]/best",
            "--restrict-filenames",
            "--no-playlist",
            "--paths", str(output_path),
            "--output", "%(title)s-%(id)s.%(ext)s",
            "--print", "after_move:filepath",
            video_url,
        ]
        logger.info("Executing yt-dlp command: %s", " ".join(command))
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120, **SUBPROCESS_TEXT_KWARGS)
        logger.info("yt-dlp stdout: %s", result.stdout)
        if result.stderr:
            logger.warning("yt-dlp stderr: %s", result.stderr)

        paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return resolve_downloaded_video_path(video_url, output_path, paths)
    except subprocess.CalledProcessError as exc:
        if is_geo_restriction_error(exc.stderr):
            raise GeoRestrictedVideoError(str(exc.stderr or "")) from None
        if any(term in str(exc.stderr or "").casefold() for term in BOT_VERIFICATION_TERMS):
            raise VideoDownloadBlockedError(str(exc.stderr or "")) from None
        logger.error("yt-dlp failed to download %s: %s (stderr: %s)", video_url, exc, exc.stderr)
        return None
    except subprocess.TimeoutExpired:
        logger.error("Video download timed out.")
        return None
    except FileNotFoundError:
        logger.error("yt-dlp command not found. Please ensure yt-dlp is installed and in your PATH.")
        return None
    except Exception as exc:
        logger.error("An unexpected error occurred during video download: %s", exc)
        return None


def search_and_download_youtube_video(
    query: str,
    config: dict[str, str],
    seen_video_ids: set[str],
    preferred_url: str = "",
    trusted_source: str = "",
    prefer_geo_fallback_order: bool = False,
    validation_mode: str = "official",
    highlights_fallback: bool = False,
    cazetv_news_fallback: bool = False,
    eligibility_title: str = "",
) -> tuple[str | None, str | None]:
    """Searches YouTube for a video and downloads the best match, skipping duplicates.

    Args:
        query: The search query for the YouTube video.
        config: The application configuration dictionary.
        seen_video_ids: A set of video IDs that have already been seen/downloaded.

    Returns:
        A tuple containing (downloaded_file_path, video_url) or (None, None).
    """
    title_to_check = eligibility_title or query
    if not is_download_eligible_title(title_to_check):
        logger.info("Skipping YouTube download because the article title is not download-eligible: %s", title_to_check)
        return None, None

    yt_dlp_bin = config.get("yt_dlp_bin", YT_DLP_BIN)
    downloads_dir = config.get("downloads_dir", DOWNLOADS_DIR)
    geo_restriction_seen = highlights_fallback

    try:
        if channel_identity(trusted_source) == channel_identity("CazéTV") and not cazetv_news_fallback:
            logger.info("CazéTV is news-only; skipping its original video and searching other trusted highlights.")
            return search_and_download_youtube_video(
                query,
                config,
                seen_video_ids,
                cazetv_news_fallback=True,
            )

        if preferred_url:
            if channel_identity(trusted_source) == channel_identity("CazéTV"):
                logger.info(
                    "YouTube candidate | title=%s | uploader=%s | url=%s",
                    query, trusted_source, preferred_url,
                )
                if not is_relevant_video_candidate({"title": query}, query):
                    logger.info("Skipping CazéTV video because it contains rejected content terms: %s", query)
                    logger.info("No trusted official video found.")
                    return None, None
                logger.info("Official channel found: %s", trusted_source)
                logger.info("Downloading...")
                try:
                    downloaded_path = download_youtube_video(preferred_url, Path(downloads_dir), str(yt_dlp_bin))
                except GeoRestrictedVideoError:
                    logger.warning("Official download is geo-restricted.")
                    logger.warning("Trying next trusted official channel...")
                    return search_and_download_youtube_video(
                        query,
                        config,
                        seen_video_ids,
                        prefer_geo_fallback_order=True,
                        highlights_fallback=True,
                    )
                return (downloaded_path, preferred_url) if downloaded_path else (None, None)

            logger.info("Inspecting article YouTube URL before download...")
            metadata_command = [
                str(yt_dlp_bin), preferred_url, "--dump-single-json", "--skip-download",
                "--no-warnings", "--quiet",
            ]
            try:
                metadata_result = subprocess.run(
                    metadata_command, capture_output=True, text=True, check=True, timeout=20, **SUBPROCESS_TEXT_KWARGS
                )
            except subprocess.TimeoutExpired:
                logger.warning("Article video inspection timed out. Falling back to YouTube search.")
                return search_and_download_youtube_video(query, config, seen_video_ids)
            except subprocess.CalledProcessError as exc:
                if is_geo_restriction_error(exc.stderr):
                    logger.warning("Official download is geo-restricted.")
                    logger.warning("Trying next trusted official channel...")
                    return search_and_download_youtube_video(
                        query,
                        config,
                        seen_video_ids,
                        prefer_geo_fallback_order=channel_identity(trusted_source) == channel_identity("CazéTV"),
                        highlights_fallback=True,
                    )
                logger.warning("Article video inspection failed. Falling back to YouTube search.")
                return search_and_download_youtube_video(query, config, seen_video_ids)
            metadata = json.loads(metadata_result.stdout)
            uploader = metadata.get("channel") or metadata.get("uploader") or "unknown"
            logger.info(
                "YouTube candidate | title=%s | uploader=%s | url=%s",
                metadata.get("title", ""), uploader, preferred_url,
            )
            valid_download, download_reason = validate_youtube_download_candidate(metadata)
            if not valid_download:
                logger.info("Skipping YouTube download because %s: %s", download_reason, uploader)
                logger.info("Falling back to trusted YouTube search.")
                return search_and_download_youtube_video(query, config, seen_video_ids)
            logger.info("Official channel found: %s", uploader)
            if not is_relevant_video_candidate(metadata, query):
                logger.info("Skipping official-channel video because it is not relevant to the article: %s", metadata.get("title", ""))
                logger.info("Falling back to trusted YouTube search.")
                return search_and_download_youtube_video(query, config, seen_video_ids)
            logger.info("Downloading...")
            try:
                downloaded_path = download_youtube_video(preferred_url, Path(downloads_dir), str(yt_dlp_bin))
            except GeoRestrictedVideoError:
                logger.warning("Official download is geo-restricted.")
                logger.warning("Trying next trusted official channel...")
                geo_restriction_seen = True
                return search_and_download_youtube_video(
                    query,
                    config,
                    seen_video_ids,
                    prefer_geo_fallback_order=channel_identity(uploader) == channel_identity("CazéTV"),
                    highlights_fallback=True,
                )
            if downloaded_path:
                return downloaded_path, preferred_url
            logger.info("Article video download failed. Falling back to trusted YouTube search.")
            return search_and_download_youtube_video(query, config, seen_video_ids)

        team_names = extract_match_teams(query)
        if validation_mode == "highlights_discovery" or cazetv_news_fallback:
            if validation_mode == "highlights_discovery":
                logger.info("Searching non-official match highlights...")
            else:
                logger.info("Searching match highlights...")
            normalized_query = normalize_channel_name(query)
            if team_names:
                if "champions" in normalized_query:
                    competition = "Champions League"
                elif "brasileir" in normalized_query:
                    competition = "Campeonato Brasileiro"
                else:
                    competition = "FIFA World Cup"
                search_queries = build_match_highlight_queries(*team_names, competition)
            else:
                search_queries = [query if any(term in normalized_query for term in HIGHLIGHT_TITLE_TERMS) else f"{query} highlights"]
        else:
            logger.info("Searching official YouTube video...")
            search_queries = [query]

        configured_match_queries = config.get("match_queries", [])
        if isinstance(configured_match_queries, list) and configured_match_queries:
            search_queries = [str(item) for item in configured_match_queries if str(item).strip()]

        video_metadata: list[dict[str, Any]] = []
        for search_query in search_queries:
            logger.info("Search query: %s", search_query)
            search_command = [
                yt_dlp_bin, f"ytsearch20:{search_query}", "--dump-json",
                "--flat-playlist", "--no-warnings", "--quiet",
            ]
            logger.debug("Executing yt-dlp search command: %s", " ".join(search_command))
            search_result = subprocess.run(
                search_command, capture_output=True, text=True, check=True, timeout=20, **SUBPROCESS_TEXT_KWARGS
            )
            video_metadata.extend(
                json.loads(line) for line in search_result.stdout.strip().split('\n') if line.strip()
            )

        if validation_mode == "highlights_discovery":
            official_candidates = rank_highlight_candidates(video_metadata, team_names)
        else:
            if cazetv_news_fallback:
                channel_priority = CAZETV_NEWS_FALLBACK_CHANNEL_ORDER
            else:
                channel_priority = GEO_RESTRICTED_FALLBACK_CHANNEL_ORDER if prefer_geo_fallback_order else None
            official_candidates = rank_official_youtube_candidates(
                video_metadata, query, seen_video_ids, channel_priority=channel_priority
            )
        for selected_video in official_candidates:
            best_video_id = str(selected_video.get("id"))
            best_video_url = selected_video.get("webpage_url") or selected_video.get("url")
            if best_video_url == best_video_id:
                best_video_url = f"https://www.youtube.com/watch?v={best_video_id}"
            if not best_video_url:
                best_video_url = f"https://www.youtube.com/watch?v={best_video_id}"
            logger.info("Downloading...")
            try:
                downloaded_path = download_youtube_video(best_video_url, Path(downloads_dir), str(yt_dlp_bin))
            except GeoRestrictedVideoError:
                logger.warning("Official download is geo-restricted.")
                logger.warning("Trying next trusted official channel...")
                geo_restriction_seen = True
                selected_uploader = selected_video.get("channel") or selected_video.get("uploader") or ""
                if channel_identity(selected_uploader) == channel_identity("CazéTV") and not prefer_geo_fallback_order:
                    return search_and_download_youtube_video(
                        query,
                        config,
                        seen_video_ids | {best_video_id},
                        prefer_geo_fallback_order=True,
                        highlights_fallback=True,
                    )
                continue
            if downloaded_path:
                return downloaded_path, best_video_url
        if validation_mode == "official" and (geo_restriction_seen or cazetv_news_fallback) and team_names:
            return search_and_download_youtube_video(
                query,
                config,
                seen_video_ids,
                validation_mode="highlights_discovery",
            )
        logger.info("No trusted official video found.")
        return None, None

    except subprocess.CalledProcessError as exc:
        if preferred_url:
            logger.warning("Article video inspection failed. Falling back to YouTube search.")
            return search_and_download_youtube_video(query, config, seen_video_ids)
        logger.error("yt-dlp search failed for query %s: %s", query, exc.stderr)
        logger.info("No trusted official video found.")
        return None, None
    except FileNotFoundError:
        logger.error("yt-dlp command not found. Please ensure yt-dlp is installed and in your PATH.")
        logger.info("No trusted official video found.")
        return None, None
    except Exception as exc:
        if preferred_url:
            logger.warning("Article video inspection failed. Falling back to YouTube search.")
            return search_and_download_youtube_video(query, config, seen_video_ids)
        logger.error("An unexpected error occurred during YouTube search/download: %s", exc)
        logger.info("No trusted official video found.")
        return None, None

    return None, None

def run_forever() -> None:
    """Run the monitoring loop every 15 minutes until interrupted."""
    load_dotenv()
    config = load_config()

    # Safe debug info: indicate whether Telegram credentials are configured (do not log values)
    bot_cfg = bool(config.get("telegram_bot_token"))
    chat_cfg = bool(config.get("telegram_chat_id"))
    logger.info("Telegram bot token configured: %s", "yes" if bot_cfg else "no")
    logger.info("Telegram chat id configured: %s", "yes" if chat_cfg else "no")

    while True:
        try:
            # Quick scan for live matches and adjust polling interval when any are active
            try:
                live_matches = find_live_matches_from_feeds()
            except Exception:
                live_matches = []

            if live_matches:
                logger.info("Live matches detected (%d). Polling every 120s for immediate events.", len(live_matches))
                process_cycle(config)
                sleep_interval = 120
            else:
                process_cycle(config)
                sleep_interval = CHECK_INTERVAL_SECONDS
        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user")
            raise
        except Exception as exc:  # pragma: no cover - protects long-running loop
            logger.exception("Unexpected failure in monitoring loop: %s", exc)

        logger.info("Sleeping for %s seconds", sleep_interval)
        time.sleep(sleep_interval)


def main() -> int:
    """CLI entry point."""
    load_dotenv()
    clear_official_rss_cache()
    config = load_config()
    reset_persistent_state_runtime()
    load_persistent_state(config, force_reload=True)

    # Safe debug info: indicate whether Telegram credentials are configured (do not log values)
    bot_cfg = bool(config.get("telegram_bot_token"))
    chat_cfg = bool(config.get("telegram_chat_id"))
    logger.info("Telegram bot token configured: %s", "yes" if bot_cfg else "no")
    logger.info("Telegram chat id configured: %s", "yes" if chat_cfg else "no")

    debug_mode = False
    args = sys.argv[1:]
    if "--debug" in args:
        debug_mode = True
        config["debug_mode"] = True

    if "--telegram-poll-once" in args:
        telegram_poll_once(config)
        return 0

    if "--telegram-poll" in args:
        telegram_poll(config)
        return 0

    if "--clear-telegram-short-state" in args:
        state = load_persistent_state(config)
        if state is not None:
            state["processed_telegram_shorts"] = {}
            save_persistent_state(config)
        logger.info("Telegram short state cleared")
        return 0

    if "--reset-seen" in args:
        state_file = Path(config.get("state_file", STATE_FILE))
        save_seen_articles(state_file, set())
        print(f"Seen cache reset: {state_file}")
        return 0

    process_url_index = args.index("--process-url") + 1 if "--process-url" in args else None
    if process_url_index is not None:
        if process_url_index >= len(args):
            logger.error("--process-url requires a URL")
            return 1
        process_url = args[process_url_index].strip()
        if not is_instagram_video_url(process_url):
            logger.error("--process-url currently supports Instagram reel, post, and TV URLs only: %s", process_url)
            return 1
        return 0 if process_instagram_video_source({
            "title": "Instagram football video",
            "source": "Instagram",
            "url": process_url,
        }, config) else 1

    process_file_index = args.index("--process-file") + 1 if "--process-file" in args else None
    if process_file_index is not None:
        if process_file_index >= len(args):
            logger.error("--process-file requires a local MP4 path")
            return 1
        return 0 if process_local_video_file(args[process_file_index], config) else 1

    if "--once" in args:
        process_telegram_commands(config)
        process_cycle(config)
        return 0

    manual_index = args.index("--manual") + 1 if "--manual" in args else None
    if manual_index is not None and manual_index < len(args):
        headline = args[manual_index].strip()
        if not headline:
            logger.error("Manual headline cannot be empty")
            return 1
        grouped_article = build_manual_grouped_article(headline)
        build_portuguese_shorts_pack(grouped_article, config)
        send_telegram_notification(grouped_article, config)
        return 0

    if debug_mode:
        process_cycle(config)
        return 0

    run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


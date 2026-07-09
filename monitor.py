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
YOUTUBE_DOWNLOAD_TITLE_TERMS = ("match highlights", "extended highlights", "highlights")
YOUTUBE_DOWNLOAD_REJECTED_TERMS = (
    "analysis", "reaction", "live", "podcast", "debate", "preview",
    "press conference", "opinion", "heroics", "greatest comeback", "daily",
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
    return bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized))


def validate_youtube_download_candidate(metadata: dict[str, Any]) -> tuple[bool, str]:
    """Allow downloads only for genuine TVNZ Sport match-highlight videos."""
    uploader_names = {
        channel_identity(metadata.get("channel")),
        channel_identity(metadata.get("uploader")),
    }
    if channel_identity(YOUTUBE_DOWNLOAD_CHANNEL) not in uploader_names:
        return False, "uploader is not TVNZ Sport"

    title = metadata.get("title") or ""
    for term in YOUTUBE_DOWNLOAD_REJECTED_TERMS:
        if _contains_normalized_phrase(title, term):
            return False, f"title contains rejected term: {term}"
    if not any(_contains_normalized_phrase(title, term) for term in YOUTUBE_DOWNLOAD_TITLE_TERMS):
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

    video_search_queries = [
        f"{original_title} official video",
        f"{original_title} FIFA official",
        f"{original_title} ESPN official",
        f"{original_title} BBC Sport official",
    ]
    video_search_links = [build_video_search_url(query) for query in video_search_queries]

    # Initialize video_links if it doesn't exist
    if "video_links" not in grouped_article or not isinstance(grouped_article["video_links"], list):
        grouped_article["video_links"] = []

    existing_video_url = grouped_article.get("video_url") or grouped_article.get("link") or ""
    if existing_video_url and ("youtube.com" in existing_video_url or "youtu.be" in existing_video_url):
        if existing_video_url not in grouped_article["video_links"]:
            grouped_article["video_links"].append(existing_video_url)
        grouped_article["video_url"] = existing_video_url
    else:
        # If no explicit video URL is found, prioritize YouTube search
        official_video_url = find_official_video_url(grouped_article)
        if official_video_url and official_video_url not in grouped_article["video_links"]:
            grouped_article["video_links"].append(official_video_url)
        grouped_article["video_url"] = official_video_url

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
    official_sources_block = ", ".join(official_sources) if official_sources else "Não informado"
    hashtags_block = " ".join(shorts_pack.get('hashtags', [])) or "#Futebol #ShortsFutebol"
    scripts = shorts_pack.get('narration_scripts', {}) or {}

    return (
        f"🚨 {title}\n\n"
        f"📰 Notícias\n"
        f"✅ {official_sources_block}\n\n"
        f"🎥 Vídeos\n"
        f"▶ {' | '.join(video_links) if video_links else 'Não informado'}\n\n"
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
        f"*Ideia para Shorts:* {shorts_pack.get('shorts_title', '')}"
    ).strip()


def build_manual_telegram_message(grouped_article: dict[str, Any], config: dict[str, str]) -> str:
    """Build a Brazilian Portuguese Telegram alert for a manual breaking-news trigger."""
    shorts_pack = build_portuguese_shorts_pack(grouped_article, config)
    search_links = build_search_links(grouped_article)
    return (
        "*⚡ Alerta Manual — Breaking News*\n\n"
        f"*🚨 Viral Score:* {shorts_pack.get('score', 0)}/10\n"
        f"*📺 Source:* {', '.join(grouped_article.get('sources', []))}\n"
        f"*🔗 Original URL:* {grouped_article.get('link') or grouped_article.get('links', [''])[0]}\n"
        f"*🎥 YouTube URL:* {grouped_article.get('video_url', '')}\n"
        f"*🔎 YouTube search link:* {search_links[0]}\n"
        f"*🔎 FIFA/ESPN/BBC/Google search links:* {' | '.join(search_links[1:])}\n"
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
    video_search_link = grouped_article.get("video_search_link") or ""

    shorts_pack = build_portuguese_shorts_pack(grouped_article, config)
    video_url = original_video_url or grouped_article.get("video_url") or shorts_pack.get('video_url', "")
    if not video_search_link:
        video_search_link = build_video_search_url(f"{title} CazéTV")
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
        f"*🔎 Link de busca do YouTube:* {video_search_link}\n"
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

    if not token or not chat_id:
        logger.warning("Telegram credentials are not configured. Skipping notification for %s", notification_title)
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
        search_links = build_search_links(grouped_article)
        downloaded_video_path = grouped_article.get("downloaded_video_path", "")
        download_block = f"*💾 Vídeo baixado:* {downloaded_video_path}\n" if downloaded_video_path else ""

        message = (
            "*⚡ Alerta de Conteúdo — Futeba & Juninho*\n\n"
            f"*🚨 Viral Score:* {shorts_pack.get('score', 0)}/10\n"
            f"*📺 Source:* {sources}\n"
            f"*🔗 Original URL:* {grouped_article.get("link") or grouped_article.get("links", [""])[0]}\n"
            f"*🎥 Link do vídeo oficial:* {shorts_pack.get('video_url', "")}\n"
            f"*🔎 YouTube search link:* {search_links[0]}\n"
            f"*🔎 FIFA/ESPN/BBC/Google search links:* {" | ".join(search_links[1:])}\n"
            f"{download_block}"
            f"*📰 Notícia:* {notification_title}\n"
            f"*🖼 Melhor thumbnail:* {shorts_pack.get('thumbnail_frame_idea', "")}\n"
            f"*🎙 Narração HeyGen:* {shorts_pack.get('heygen_narration', "")}\n"
            f"*📜 Prompt para HeyGen:* {heygen_prompt}\n"
            f"*🎬 Prompt para Veo 3/Kling:* {veo_prompt}\n"
            f"*📝 Descrição YouTube:* {shorts_pack.get('description', "")}\n"
            f"*🏷 Hashtags:* {" ".join(shorts_pack.get('hashtags', []))}\n"
            f"*📌 Título:* {shorts_pack.get('shorts_title', "")}\n"
            f"*⏱ Tempo estimado:* 30s, 45s, 60s\n"
            f"*🎙 30s:* {shorts_pack.get('narration_scripts', {}).get('30s', '')}\n"
            f"*🎙 45s:* {shorts_pack.get('narration_scripts', {}).get('45s', '')}\n"
            f"*🎙 60s:* {shorts_pack.get('narration_scripts', {}).get('60s', '')}\n"
            f"*🔍 Search keywords:* {", ".join(shorts_pack.get('search_keywords', []))}\n"
            f"*🔥 Potencial viral:* {shorts_pack.get('score', 0)}/10\n"
            f"*💡 Por que vale postar:* {shorts_pack.get('viral_reason', "")}\n"
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
            return False

    except requests.exceptions.RequestException:
        logger.error("Telegram request failed before a valid response was received for %s", notification_title)
        return False
    except Exception:
        logger.error("Unexpected Telegram failure for %s", notification_title)
        return False

    logger.info("Telegram notification sent for %s", notification_title)
    return True


def build_downloaded_video_caption(story: dict[str, Any]) -> str:
    """Build a compact caption for a downloaded video sent to Telegram."""
    sources = ", ".join(str(source) for source in story.get("sources", []) if str(source))
    original_url = story.get("link") or next((link for link in story.get("links", []) if link), "")
    lines = [
        f"Title: {story.get('title', '')}",
        f"Source: {sources or story.get('source', '') or story.get('official_source', '')}",
        f"Content category: {story.get('content_category', '') or 'UNKNOWN'}",
    ]
    if original_url:
        lines.append(f"Original URL: {original_url}")
    return "\n".join(lines)[:1024]


def send_downloaded_video_to_telegram(file_path: str | Path, story: dict[str, Any]) -> bool:
    """Send a downloaded MP4 to Telegram, falling back safely when upload fails or is too large."""
    telegram_config = story.get("_telegram_config") if isinstance(story.get("_telegram_config"), dict) else load_config()
    token = str(telegram_config.get("telegram_bot_token", "") or "")
    chat_id = str(telegram_config.get("telegram_chat_id", "") or "")
    title = str(story.get("title", "") or "").strip()
    if not token or not chat_id:
        logger.warning("Telegram credentials are not configured. Skipping downloaded video delivery for %s", title)
        return False

    path = Path(file_path)
    try:
        if not path.exists() or not path.is_file():
            logger.warning("Downloaded video file not found for Telegram delivery: %s", path)
            return False
        if path.stat().st_size > TELEGRAM_VIDEO_FILE_LIMIT_BYTES:
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
                return False
            return True

        caption = build_downloaded_video_caption(story).replace(token, "[REDACTED]")
        for method, field_name in (("sendVideo", "video"), ("sendDocument", "document")):
            with path.open("rb") as video_file:
                response = requests.post(
                    f"https://api.telegram.org/bot{token}/{method}",
                    data={"chat_id": chat_id, "caption": caption},
                    files={field_name: video_file},
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            if response.status_code == 200:
                logger.info("Downloaded video sent to Telegram with %s for %s", method, title)
                return True
            logger.error("Telegram %s\nResponse:\n%s", response.status_code, str(response.text or "").replace(token, "[REDACTED]"))
            if method == "sendVideo":
                logger.info("Telegram sendVideo failed. Retrying with sendDocument.")
        return False
    except requests.exceptions.RequestException:
        logger.error("Telegram downloaded video delivery failed for %s", title)
        return False
    except Exception:
        logger.error("Unexpected Telegram downloaded video delivery failure for %s", title)
        return False


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
    downloaded_video_ids: set[str] = {alert.get("video_id", "") for alert in alerts if alert.get("video_id")}

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
        accepted = content_decision["should_alert"] and (should_send_notification(scored_article) or is_live_goal_event(scored_article))
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
        alert_key = f"{article.get("title","").strip()}::{article.get("score",0)}"
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

        notification_sent = send_telegram_notification(article, config)
        if notification_sent:
            try:
                for x_post in discover_x_posts(article, config)[:3]:
                    send_x_discovery_notification(x_post, config)
            except Exception:
                logger.warning("X discovery failed unexpectedly; monitoring will continue.")
            if not debug_mode:
                for duplicate_key in article.get("duplicate_keys", []):
                    if duplicate_key:
                        persisted_seen_keys.add(str(duplicate_key))
            # Prefer an article-provided YouTube URL; otherwise search trusted channels.
            article_url = article.get("video_url") or article.get("link") or ""
            preferred_url = article_url if is_youtube_link(article_url) else ""
            article_source = article.get("official_source", "") or next(iter(article.get("sources", [])), "")
            downloaded_path = None
            video_id_for_download = "" if channel_identity(article_source) == channel_identity("CazéTV") else extract_youtube_video_id(preferred_url)
            article["downloaded_video_path"] = "" # Initialize for consistency

            if not article.get("should_download", False):
                logger.info("Skipping YouTube download because content classification disallows it: %s", article.get("content_category", "UNKNOWN"))
            elif video_id_for_download and video_id_for_download in downloaded_video_ids:
                logger.info("Skipping download for %s because video ID %s already downloaded.", article.get("title"), video_id_for_download)
            else:
                attach_article_to_match(article, today_matches)
                search_query = article.get("title", "") or "World Cup 2026 football highlights"
                download_config = dict(config)
                if article.get("match"):
                    match_queries = build_match_day_queries(article["match"])
                    if match_queries:
                        search_query = match_queries[0]
                        download_config["match_queries"] = match_queries
                        logger.info("Matched today's game: %s vs %s", article["match"]["home_team"], article["match"]["away_team"])
                        logger.info("Search query: %s", search_query)
                else:
                    logger.info("Using article title fallback.")
                    logger.info("Search query: %s", search_query)
                downloaded_path, actual_video_url = search_and_download_youtube_video(
                    search_query,
                    download_config,
                    downloaded_video_ids,
                    preferred_url=preferred_url,
                    trusted_source=article_source,
                    eligibility_title=article.get("title", ""),
                )

                if downloaded_path:
                    article["downloaded_video_path"] = downloaded_path
                    article["video_url"] = actual_video_url
                    downloaded_video_id = extract_youtube_video_id(actual_video_url or "") or video_id_for_download
                    if downloaded_video_id:
                        article["video_id"] = downloaded_video_id
                        downloaded_video_ids.add(downloaded_video_id)
                    videos_downloaded += 1
                    logger.info("Downloaded video to %s for %s", downloaded_path, article.get("title"))
                    article["_telegram_config"] = config
                    send_downloaded_video_to_telegram(downloaded_path, article)
                    article.pop("_telegram_config", None)
                else:
                    logger.info("No trusted official video downloaded for %s", article.get("title"))

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
                "downloaded_video_path": downloaded_path,  # Store path to downloaded video
            })
            seen_alert_keys.add(alert_key)
            notified_stories_in_cycle.append(article) # Add to notified list for deduplication in current cycle

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
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=120)
        logger.info("yt-dlp stdout: %s", result.stdout)
        if result.stderr:
            logger.warning("yt-dlp stderr: %s", result.stderr)

        paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return paths[-1] if paths else None
    except subprocess.CalledProcessError as exc:
        if is_geo_restriction_error(exc.stderr):
            raise GeoRestrictedVideoError(str(exc.stderr or "")) from None
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
                    metadata_command, capture_output=True, text=True, check=True, timeout=20
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
                search_command, capture_output=True, text=True, check=True, timeout=20
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
    config = load_config()

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

    if "--reset-seen" in args:
        state_file = Path(config.get("state_file", STATE_FILE))
        save_seen_articles(state_file, set())
        print(f"Seen cache reset: {state_file}")
        return 0

    if "--once" in args:
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


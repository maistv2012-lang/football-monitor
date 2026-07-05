from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import feedparser
import requests
from dotenv import load_dotenv

from config import (
    CHECK_INTERVAL_SECONDS,
    KEYWORDS,
    REQUEST_TIMEOUT_SECONDS,
    STATE_FILE,
    TELEGRAM_BOT_TOKEN_ENV,
    TELEGRAM_CHAT_ID_ENV,
    USER_AGENT,
    VIRAL_SCORE_THRESHOLD,
    build_youtube_feed_url,
    get_feeds,
    load_config,
    normalize_text,
)
def build_priority_youtube_queries():
    priority_teams = [
        "Brazil", "Argentina", "Portugal", "France", "Spain",
        "Colombia", "Mexico", "England", "Germany", "Uruguay",
    ]

    priority_players = [
        "Messi", "Cristiano Ronaldo", "Neymar", "Mbappe",
        "Vini Jr", "Rodrygo", "Endrick", "Lamine Yamal",
        "Luis Diaz", "Bellingham",
    ]

    event_terms = [
        "goal World Cup 2026",
        "highlights World Cup 2026",
        "VAR World Cup 2026",
        "red card World Cup 2026",
        "penalty World Cup 2026",
        "funny moment World Cup 2026",
        "fan reaction World Cup 2026",
    ]

    queries = []

    for team in priority_teams:
        for term in event_terms:
            queries.append(f"{team} {term}")

    for player in priority_players:
        queries.append(f"{player} goal World Cup 2026")
        queries.append(f"{player} highlights World Cup 2026")
        queries.append(f"{player} funny moment World Cup 2026")

    queries.extend([
        "gol Brasil Copa 2026",
        "Brasil melhores momentos Copa 2026",
        "gol Argentina Copa 2026",
        "Argentina melhores momentos Copa 2026",
        "gol Portugal Copa 2026",
        "gol França Copa 2026",
        "gol Espanha Copa 2026",
        "gol Colombia Copa 2026",
        "gol Mexico Copa 2026",
    ])

    return list(dict.fromkeys(queries))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("football-monitor")


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
        if not group.get("official_source"):
            group["official_source"] = group["sources"][0] if group.get("sources") else ""

    return groups


def is_youtube_link(value: str) -> bool:
    """Return True when the provided value points to YouTube."""
    normalized = (value or "").strip().lower()
    return "youtube.com" in normalized or "youtu.be" in normalized


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

    if any(source_name == "cazétv" or source_name == "caze" or source_name == "cazétv" for source_name in source_names):
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

    existing_video_url = grouped_article.get("video_url") or grouped_article.get("link") or ""
    if existing_video_url and ("youtube.com" in existing_video_url or "youtu.be" in existing_video_url):
        grouped_article["video_url"] = existing_video_url
    else:
        grouped_article["video_url"] = find_official_video_url(grouped_article)

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
    reason = grouped_article.get("reason", "") or shorts_pack.get("viral_reason", "")
    viral_score = grouped_article.get("viral_score", shorts_pack.get("viral_score", 0)) or 0
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
    hashtags_block = " ".join(shorts_pack.get("hashtags", [])) or "#Futebol #ShortsFutebol"
    scripts = shorts_pack.get("narration_scripts", {}) or {}

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
    video_url = original_video_url or grouped_article.get("video_url") or shorts_pack.get("video_url", "")
    if not video_search_link:
        video_search_link = build_video_search_url(f"{title} CazéTV")
    search_keywords = original_search_keywords or grouped_article.get("search_keywords") or shorts_pack.get("search_keywords", [])

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

    if grouped_article.get("is_manual_event"):
        message = build_manual_telegram_message(grouped_article, config)
    elif grouped_article.get("is_live_event") or any(
        is_live_goal_event({"title": grouped_article.get("title", ""), "source": source})
        for source in grouped_article.get("sources", [])
    ):
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
        message = (
            "*⚡ Alerta de Conteúdo — Futeba & Juninho*\n\n"
            f"*🚨 Viral Score:* {shorts_pack.get('score', 0)}/10\n"
            f"*📺 Source:* {sources}\n"
            f"*🔗 Original URL:* {grouped_article.get('link') or grouped_article.get('links', [''])[0]}\n"
            f"*🎥 Link do vídeo oficial:* {shorts_pack.get('video_url', '')}\n"
            f"*🔎 YouTube search link:* {search_links[0]}\n"
            f"*🔎 FIFA/ESPN/BBC/Google search links:* {' | '.join(search_links[1:])}\n"
            f"*📰 Notícia:* {notification_title}\n"
            f"*🖼 Melhor thumbnail:* {shorts_pack.get('thumbnail_frame_idea', '')}\n"
            f"*🎙 Narração HeyGen:* {shorts_pack.get('heygen_narration', '')}\n"
            f"*📜 Prompt para HeyGen:* {heygen_prompt}\n"
            f"*🎬 Prompt para Veo 3/Kling:* {veo_prompt}\n"
            f"*📝 Descrição YouTube:* {shorts_pack.get('description', '')}\n"
            f"*🏷 Hashtags:* {' '.join(shorts_pack.get('hashtags', []))}\n"
            f"*📌 Título:* {shorts_pack.get('shorts_title', '')}\n"
            f"*⏱ Tempo estimado:* 30s, 45s, 60s\n"
            f"*🎙 30s:* {shorts_pack.get('narration_scripts', {}).get('30s', '')}\n"
            f"*🎙 45s:* {shorts_pack.get('narration_scripts', {}).get('45s', '')}\n"
            f"*🎙 60s:* {shorts_pack.get('narration_scripts', {}).get('60s', '')}\n"
            f"*🔍 Search keywords:* {', '.join(shorts_pack.get('search_keywords', []))}\n"
            f"*🔥 Potencial viral:* {shorts_pack.get('score', 0)}/10\n"
            f"*💡 Por que vale postar:* {shorts_pack.get('viral_reason', '')}\n"
            f"*📰 Link da notícia:* {links}\n"
            f"*Source:* {sources}"
        ).strip()
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Telegram notification failed: %s", exc)
        return False

    logger.info("Telegram notification sent for %s", notification_title)
    return True


def process_cycle(config: dict[str, str]) -> int:
    """Fetch feeds, filter relevant stories, group duplicates, score them, and notify on high viral potential."""
    debug_mode = bool(config.get("debug_mode", False))
    state_file = Path(config.get("state_file", STATE_FILE))
    alerts_file = Path(config.get("alerts_file", STATE_FILE))
    seen_articles = load_seen_articles(state_file)
    alerts = load_alerts(alerts_file)
    seen_alert_keys = {alert.get("alert_key", "") for alert in alerts if alert.get("alert_key")}
    seen_this_cycle: set[str] = set()
    relevant_articles: list[dict[str, str]] = []
    persisted_seen_keys: set[str] = set()

    logger.info("Starting monitoring cycle")
    warned_about_feeds: set[str] = set()
    sources_checked = 0
    videos_found = 0
    articles_found = 0
    stories_rejected = 0

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
            latest_titles.append(article.get("title", ""))
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
        accepted = should_send_notification(scored_article) or is_live_goal_event(scored_article)
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

    for article in high_potential_articles:
        alert_key = f"{article.get('title','').strip()}::{article.get('score',0)}"
        if alert_key in seen_alert_keys:
            logger.info("Skipping duplicate alert for %s", article.get("title"))
            continue
        notification_sent = send_telegram_notification(article, config)
        if notification_sent:
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
                "video_search_links": article.get("video_search_links", []),
                "links": article.get("links", []),
                "sources": article.get("sources", []),
            })
            seen_alert_keys.add(alert_key)

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
        print(f"FINAL HIGH POTENTIAL STORIES: {len(high_potential_articles)}")
        print(f"Sources checked: {sources_checked}")
        print(f"Videos found: {videos_found}")
        print(f"Articles found: {articles_found}")
        print(f"Stories merged: {len(grouped_articles)}")
        print(f"Stories rejected: {stories_rejected}")
        print(f"Final high-potential stories: {len(high_potential_articles)}")
        print(f"notícias verificadas: {sources_checked}")
        print(f"vídeos encontrados: {videos_found}")
        print(f"artigos encontrados: {articles_found}")
        print(f"histórias mescladas: {len(grouped_articles)}")
        print(f"histórias rejeitadas: {stories_rejected}")
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
            print(f"{index}️⃣ {title} — Viral Score: {score}")

    logger.info("Monitoring cycle finished. High-potential stories: %s", len(high_potential_articles))
    return len(high_potential_articles)


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

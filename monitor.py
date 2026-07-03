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
    FEEDS,
    KEYWORDS,
    REQUEST_TIMEOUT_SECONDS,
    STATE_FILE,
    TELEGRAM_BOT_TOKEN_ENV,
    TELEGRAM_CHAT_ID_ENV,
    USER_AGENT,
    VIRAL_SCORE_THRESHOLD,
    load_config,
    normalize_text,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("football-monitor")


def is_relevant_article(article: dict[str, str]) -> bool:
    """Return True when the title contains an important football short-form topic."""
    title = normalize_text(article.get("title", ""))
    if not title:
        return False

    if any(keyword.lower() in title for keyword in KEYWORDS):
        return True

    low_value_terms = ["training", "coach", "manager", "tactics", "preview", "analysis", "opinion", "interview", "podcast"]
    if any(term in title for term in low_value_terms):
        return False

    high_value_terms = ["goal", "injury", "transfer", "bomb", "celebration", "emotional", "controversy", "red card", "var", "penalty", "fan reaction", "fight", "funny"]
    return any(term in title for term in high_value_terms)


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
                    "summary": article.get("summary", "") or article.get("description", ""),
                    "description": article.get("description", ""),
                }
            )
        else:
            if article.get("source", "") not in match["sources"]:
                match["sources"].append(article.get("source", ""))
            if article.get("link", "") and article.get("link", "") not in match["links"]:
                match["links"].append(article.get("link", ""))

    return groups


def should_send_notification(grouped_article: dict[str, Any]) -> bool:
    """Only notify Telegram for viral-potential stories above the threshold."""
    score = float(grouped_article.get("score", 0) or 0)
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


def build_portuguese_shorts_pack(grouped_article: dict[str, Any], config: dict[str, str]) -> dict[str, Any]:
    """Create a complete Portuguese-BR Shorts package for a high-impact football article."""
    original_title = grouped_article.get("title", "")
    original_source = ", ".join(grouped_article.get("sources", []))
    summary = grouped_article.get("summary", "") or grouped_article.get("description", "")
    score = grouped_article.get("score", 0)
    reason = grouped_article.get("reason", "")

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
    grouped_article["video_search_links"] = video_search_links
    grouped_article["video_url"] = find_official_video_url(grouped_article)
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
            score = 8 if any(token in normalize_text(title) for token in ["red card", "penalty", "controversy", "dramatic reaction", "fan reaction", "funny moment", "referee mistake", "fight"]) else 7
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
        return grouped_article
    except Exception as exc:
        logger.warning("AI scoring failed, falling back to heuristic: %s", exc)
        grouped_article["score"] = 0.0
        grouped_article["reason"] = "AI scoring unavailable; fallback used."
        return grouped_article


def build_article_key(article: dict[str, str], source: str) -> str:
    """Build a stable identifier so duplicates can be filtered consistently."""
    link = article.get("link") or article.get("id") or article.get("title") or ""
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
    return {
        "title": get_entry_value(entry, "title", "").strip(),
        "summary": get_entry_value(entry, "summary", "") or get_entry_value(entry, "description", ""),
        "description": get_entry_value(entry, "description", ""),
        "link": get_entry_value(entry, "link", ""),
        "id": get_entry_value(entry, "id", ""),
        "source": source,
    }


def fetch_feed_entries(feed_url: str) -> list[Any]:
    """Download and parse a feed URL using requests and feedparser."""
    response = requests.get(feed_url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    parsed_feed = feedparser.parse(response.content)
    return parsed_feed.entries


def send_telegram_notification(grouped_article: dict[str, Any], config: dict[str, str]) -> bool:
    """Send a Telegram message for a high-score grouped article if credentials are configured."""
    token = config.get("telegram_bot_token", "")
    chat_id = config.get("telegram_chat_id", "")

    if not token or not chat_id:
        logger.warning("Telegram credentials are not configured. Skipping notification for %s", grouped_article.get("title"))
        return False

    title = grouped_article.get("title", "")
    sources = ", ".join(grouped_article.get("sources", []))
    links = "\n".join(grouped_article.get("links", []))
    shorts_pack = build_portuguese_shorts_pack(grouped_article, config)
    heygen_prompt = (
        f"Persona: apresentador brasileiro de futebol, energia alta, olhar direto, movimentos naturais. "
        f"Contexto: {title}. "
        "Tom: empolgação, drama, humor leve, voz firme. "
        "Câmera: plano médio, gesto de apoio, pequenas pausas, expressão intensa."
    )
    veo_prompt = (
        f"Criar cenas extras para um Shorts de futebol sobre {title}. "
        "Estilo cinematográfico, cortes rápidos, reação de torcida, close em jogador, câmera dinâmica, iluminação forte, sensação de explosão."
    )
    message = (
        "*⚡ Alerta de Conteúdo — Futeba & Juninho*\n\n"
        f"*🚨 Viral Score:* {shorts_pack.get('score', 0)}/10\n"
        f"*📰 Notícia:* {title}\n"
        f"*🎥 Link do vídeo oficial:* {shorts_pack.get('video_url', '')}\n"
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

    logger.info("Telegram notification sent for %s", title)
    return True


def process_cycle(config: dict[str, str]) -> int:
    """Fetch feeds, filter relevant stories, group duplicates, score them, and notify on high viral potential."""
    state_file = Path(config.get("state_file", STATE_FILE))
    alerts_file = Path(config.get("alerts_file", STATE_FILE))
    seen_articles = load_seen_articles(state_file)
    alerts = load_alerts(alerts_file)
    seen_alert_keys = {alert.get("alert_key", "") for alert in alerts if alert.get("alert_key")}
    seen_this_cycle: set[str] = set()
    relevant_articles: list[dict[str, str]] = []

    logger.info("Starting monitoring cycle")

    for source, feed_url in FEEDS.items():
        try:
            entries = fetch_feed_entries(feed_url)
        except requests.RequestException as exc:
            logger.warning("Could not fetch %s feed: %s", source, exc)
            continue
        except Exception as exc:  # pragma: no cover - defensive guard for feedparser issues
            logger.warning("Could not parse %s feed: %s", source, exc)
            continue

        for entry in entries:
            article = normalize_entry(entry, source)
            if not is_relevant_article(article):
                continue

            article_key = build_article_key(article, source)
            if article_key in seen_articles or article_key in seen_this_cycle:
                continue

            seen_this_cycle.add(article_key)
            relevant_articles.append(article)
            logger.info("Detected new relevant article from %s: %s", source, article.get("title"))

    grouped_articles = group_articles(relevant_articles)
    high_potential_articles: list[dict[str, Any]] = []

    for grouped_article in grouped_articles:
        scored_article = score_article_with_ai(grouped_article, config)
        if should_send_notification(scored_article):
            high_potential_articles.append(scored_article)
            logger.info("High viral potential story: %s (score=%s)", scored_article.get("title"), scored_article.get("score"))

    for article in high_potential_articles:
        alert_key = f"{article.get('title','').strip()}::{article.get('score',0)}"
        if alert_key in seen_alert_keys:
            logger.info("Skipping duplicate alert for %s", article.get("title"))
            continue
        send_telegram_notification(article, config)
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

    updated_seen = seen_articles | seen_this_cycle
    save_seen_articles(state_file, updated_seen)
    save_alerts(alerts_file, alerts)

    logger.info("Monitoring cycle finished. High-potential stories: %s", len(high_potential_articles))
    return len(high_potential_articles)


def run_forever() -> None:
    """Run the monitoring loop every 15 minutes until interrupted."""
    load_dotenv()
    config = load_config()

    while True:
        try:
            process_cycle(config)
        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user")
            raise
        except Exception as exc:  # pragma: no cover - protects long-running loop
            logger.exception("Unexpected failure in monitoring loop: %s", exc)

        logger.info("Sleeping for %s seconds", CHECK_INTERVAL_SECONDS)
        time.sleep(CHECK_INTERVAL_SECONDS)


def main() -> int:
    """CLI entry point."""
    load_dotenv()
    config = load_config()

    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        process_cycle(config)
        return 0

    run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from monitor import (
    build_article_key,
    build_content_discovery_telegram_message,
    build_live_event_telegram_message,
    build_manual_grouped_article,
    build_portuguese_shorts_pack,
    build_portuguese_telegram_message,
    build_youtube_feed_url,
    calculate_viral_score,
    group_articles,
    is_live_goal_event,
    process_cycle,
    should_send_notification,
)

from monitor import save_seen_articles


class MonitorTests(unittest.TestCase):
    def test_group_articles_merges_duplicate_titles_from_multiple_sources(self):
        articles = [
            {"title": "Messi magic lights up the match", "source": "ESPN FC", "link": "https://example.com/1"},
            {"title": "Messi magic lights up the match", "source": "FIFA", "link": "https://example.com/2"},
        ]

        groups = group_articles(articles)

        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0]["sources"]), ["ESPN FC", "FIFA"])

    def test_high_score_articles_should_trigger_notification(self):
        grouped_article = {
            "score": 8.7,
            "reason": "Huge fan reaction and dramatic finish",
        }

        self.assertTrue(should_send_notification(grouped_article))

    def test_low_score_articles_should_not_trigger_notification(self):
        grouped_article = {
            "score": 8.4,
            "reason": "Routine transfer update",
        }

        self.assertFalse(should_send_notification(grouped_article))

    def test_messi_goal_articles_should_trigger_notification_even_below_threshold(self):
        grouped_article = {
            "title": "Messi scores a stunning goal in the final",
            "score": 7.2,
            "reason": "A classic Messi finish",
        }

        self.assertTrue(should_send_notification(grouped_article))

    def test_build_youtube_feed_url_uses_channel_id(self):
        self.assertEqual(
            build_youtube_feed_url("ABC123"),
            "https://www.youtube.com/feeds/videos.xml?channel_id=ABC123",
        )

    def test_cazetv_keyword_alerts_build_portuguese_message(self):
        grouped_article = {
            "title": "Messi faz golaço de falta e vira assunto",
            "summary": "Vídeo viral do CazéTV",
            "sources": ["CazéTV"],
            "links": ["https://www.youtube.com/watch?v=123"],
            "score": 6.8,
            "reason": "Tema forte em vídeo curto",
        }

        message = build_portuguese_telegram_message(grouped_article, {})

        self.assertIn("CazéTV", message)
        self.assertIn("Messi faz golaço de falta e vira assunto", message)
        self.assertIn("https://www.youtube.com/watch?v=123", message)
        self.assertIn("Shorts", message)
        self.assertIn("HeyGen", message)

    def test_cazetv_keyword_titles_should_trigger_notification(self):
        grouped_article = {
            "title": "Neymar entra em polêmica no treino",
            "sources": ["CazéTV"],
            "score": 4.2,
        }

        self.assertTrue(should_send_notification(grouped_article))

    def test_blocked_youtube_links_add_warning_and_search_guidance(self):
        grouped_article = {
            "title": "Messi faz golaço de falta",
            "summary": "Vídeo viral do CazéTV",
            "sources": ["CazéTV"],
            "links": ["https://www.youtube.com/watch?v=blocked123"],
            "video_url": "https://www.youtube.com/watch?v=blocked123",
            "video_status": "region_blocked",
            "video_search_link": "https://www.youtube.com/results?search_query=Messi+faz+gola%C3%A7o+de+falta+Caz%C3%A9TV",
            "search_keywords": ["Messi golaço CazéTV", "official clip"],
            "score": 7.1,
            "reason": "Tema forte",
        }

        message = build_portuguese_telegram_message(grouped_article, {})

        self.assertIn("⚠️ Este vídeo pode estar bloqueado na sua região.", message)
        self.assertIn("https://www.youtube.com/results?search_query=Messi+faz+gola%C3%A7o+de+falta+Caz%C3%A9TV", message)
        self.assertIn("Messi golaço CazéTV", message)
        self.assertIn("official clip", message)

    def test_live_goal_event_detection_and_message(self):
        article = {"title": "Brazil 1-0 Argentina: Messi scores in 45'", "source": "BBC Sport Football"}
        self.assertTrue(is_live_goal_event(article))

        grouped_article = {
            "title": "Brazil 1-0 Argentina: Messi scores in 45'",
            "summary": "Live goal from a high-profile match",
            "sources": ["BBC Sport Football"],
            "links": ["https://example.com/live"],
            "score": 9.4,
            "reason": "Live goal moment",
            "is_live_event": True,
            "match": "Brazil x Argentina",
            "minute": "45'",
            "goal_scorer": "Messi",
            "competition": "Amistoso",
            "official_source": "BBC Sport Football",
        }

        message = build_live_event_telegram_message(grouped_article, {})

        self.assertIn("Match", message)
        self.assertIn("Brazil x Argentina", message)
        self.assertIn("45'", message)
        self.assertIn("Messi", message)
        self.assertIn("Amistoso", message)
        self.assertIn("BBC Sport Football", message)

    def test_manual_grouped_article_builds_portuguese_shorts_package(self):
        article = build_manual_grouped_article("Cape Verde goal")

        self.assertTrue(article["is_manual_event"])
        self.assertEqual(article["title"], "Cape Verde goal")
        self.assertIn("shorts_title", article)
        self.assertIn("thumbnail_text", article)
        self.assertIn("narration_scripts", article)
        self.assertIn("description", article)
        self.assertIn("hashtags", article)
        self.assertIn("search_keywords", article)
        self.assertTrue(article["video_search_links"])

    def test_build_portuguese_shorts_pack_creates_brazilian_portuguese_content(self):
        article = {
            "title": "Messi drama in a wild finish",
            "summary": "A late controversy changed the whole match.",
            "sources": ["ESPN FC"],
            "links": ["https://example.com/article"],
            "score": 9,
            "reason": "Huge emotional finish",
        }

        pack = build_portuguese_shorts_pack(article, {})

        self.assertIn("shorts_title", pack)
        self.assertLessEqual(len(pack["shorts_title"]), 60)
        self.assertGreaterEqual(len(pack["thumbnail_text"]), 3)
        self.assertIn("narration_scripts", pack)
        self.assertIn("description", pack)
        self.assertTrue(pack["hashtags"])

    def test_build_article_key_uses_link_when_available(self):
        entry = {"link": "https://example.com/article/1", "title": "Example"}

        self.assertEqual(build_article_key(entry, "FIFA"), "FIFA:https://example.com/article/1")

    def test_process_cycle_emits_debug_output_for_each_source_and_item(self):
        with TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            alerts_file = Path(temp_dir) / "alerts.json"
            config = {
                "state_file": state_file,
                "alerts_file": alerts_file,
                "debug_mode": True,
                "telegram_bot_token": "",
                "telegram_chat_id": "",
            }
            entries = [{
                "title": "Messi scores a dramatic winner",
                "summary": "The moment is already trending.",
                "description": "The moment is already trending.",
                "link": "https://example.com/news/1",
                "id": "news-1",
                "published": "2026-07-04T10:00:00Z",
                "video_url": "https://www.youtube.com/watch?v=abc123",
            }]

            with patch("monitor.get_feeds", return_value={"FIFA": "https://example.com/feed"}), \
                 patch("monitor.fetch_feed_entries", return_value=entries), \
                 patch("monitor.load_seen_articles", return_value=set()), \
                 patch("monitor.save_seen_articles"), \
                 patch("monitor.load_alerts", return_value=[]), \
                 patch("monitor.save_alerts"), \
                 patch("monitor.send_telegram_notification", return_value=False):
                output = StringIO()
                with redirect_stdout(output):
                    process_cycle(config)

            text = output.getvalue()
            self.assertIn("==========================", text)
            self.assertIn("SOURCE", text)
            self.assertIn("Messi scores a dramatic winner", text)
            self.assertIn("Videos found:", text)
            self.assertIn("Stories merged:", text)
            self.assertIn("Final high-potential stories:", text)

    def test_process_cycle_emits_top_ten_summary_when_debug_mode_enabled(self):
        with TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            alerts_file = Path(temp_dir) / "alerts.json"
            config = {
                "state_file": state_file,
                "alerts_file": alerts_file,
                "debug_mode": True,
                "telegram_bot_token": "",
                "telegram_chat_id": "",
            }
            entries = [{
                "title": "Messi scores a dramatic winner",
                "summary": "The moment is already trending.",
                "description": "The moment is already trending.",
                "link": "https://example.com/news/1",
                "id": "news-1",
                "published": "2026-07-04T10:00:00Z",
                "video_url": "https://www.youtube.com/watch?v=abc123",
            }]

            with patch("monitor.get_feeds", return_value={"FIFA": "https://example.com/feed"}), \
                 patch("monitor.fetch_feed_entries", return_value=entries), \
                 patch("monitor.load_seen_articles", return_value=set()), \
                 patch("monitor.save_seen_articles"), \
                 patch("monitor.load_alerts", return_value=[]), \
                 patch("monitor.save_alerts"), \
                 patch("monitor.send_telegram_notification", return_value=False):
                output = StringIO()
                with redirect_stdout(output):
                    process_cycle(config)

            text = output.getvalue()
            self.assertIn("notícias verificadas", text)
            self.assertIn("vídeos encontrados", text)
            self.assertIn("Top 10 melhores oportunidades", text)
            self.assertIn("1️⃣", text)

    def test_process_cycle_treats_youtube_video_entries_as_new_videos(self):
        with TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            alerts_file = Path(temp_dir) / "alerts.json"
            config = {
                "state_file": state_file,
                "alerts_file": alerts_file,
                "debug_mode": True,
                "telegram_bot_token": "",
                "telegram_chat_id": "",
            }
            entries = [{
                "title": "GOL DELES! Messi reaction after the dramatic finish",
                "summary": "The moment is already trending.",
                "description": "The moment is already trending.",
                "published": "2026-07-04T10:00:00Z",
                "video_url": "https://www.youtube.com/watch?v=abc123",
            }]

            with patch("monitor.get_feeds", return_value={"CazéTV": "https://example.com/feed"}), \
                 patch("monitor.fetch_feed_entries", return_value=entries), \
                 patch("monitor.load_seen_articles", return_value={"CazéTV:GOL DELES! Messi reaction after the dramatic finish"}), \
                 patch("monitor.save_seen_articles"), \
                 patch("monitor.load_alerts", return_value=[]), \
                 patch("monitor.save_alerts"), \
                 patch("monitor.send_telegram_notification", return_value=False):
                output = StringIO()
                with redirect_stdout(output):
                    process_cycle(config)

            text = output.getvalue()
            self.assertIn("VIDEOS FOUND: 1", text)
            self.assertIn("FINAL HIGH POTENTIAL STORIES: 1", text)
            self.assertIn("ACCEPTED OR REJECTED", text)
            self.assertIn("accepted", text.lower())

    def test_debug_mode_does_not_persist_seen_articles(self):
        with TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            alerts_file = Path(temp_dir) / "alerts.json"
            config = {
                "state_file": state_file,
                "alerts_file": alerts_file,
                "debug_mode": True,
                "telegram_bot_token": "",
                "telegram_chat_id": "",
            }
            entries = [{
                "title": "Messi scores a dramatic winner",
                "summary": "The moment is already trending.",
                "description": "The moment is already trending.",
                "link": "https://example.com/news/1",
                "id": "news-1",
                "published": "2026-07-04T10:00:00Z",
                "video_url": "https://www.youtube.com/watch?v=abc123",
            }]

            with patch("monitor.get_feeds", return_value={"FIFA": "https://example.com/feed"}), \
                 patch("monitor.fetch_feed_entries", return_value=entries), \
                 patch("monitor.load_seen_articles", return_value=set()), \
                 patch("monitor.save_seen_articles"), \
                 patch("monitor.load_alerts", return_value=[]), \
                 patch("monitor.save_alerts"), \
                 patch("monitor.send_telegram_notification", return_value=False):
                process_cycle(config)

            self.assertFalse(state_file.exists())

    def test_normal_mode_persists_seen_articles_after_successful_notification(self):
        with TemporaryDirectory() as temp_dir:
            state_file = Path(temp_dir) / "state.json"
            alerts_file = Path(temp_dir) / "alerts.json"
            config = {
                "state_file": state_file,
                "alerts_file": alerts_file,
                "debug_mode": False,
                "telegram_bot_token": "",
                "telegram_chat_id": "",
            }
            entries = [{
                "title": "Messi scores a dramatic winner",
                "summary": "The moment is already trending.",
                "description": "The moment is already trending.",
                "link": "https://example.com/news/1",
                "id": "news-1",
                "published": "2026-07-04T10:00:00Z",
                "video_url": "https://www.youtube.com/watch?v=abc123",
            }]

            with patch("monitor.get_feeds", return_value={"FIFA": "https://example.com/feed"}), \
                 patch("monitor.fetch_feed_entries", return_value=entries), \
                 patch("monitor.load_seen_articles", return_value=set()), \
                 patch("monitor.save_seen_articles") as save_seen_mock, \
                 patch("monitor.load_alerts", return_value=[]), \
                 patch("monitor.save_alerts"), \
                 patch("monitor.send_telegram_notification", return_value=True):
                process_cycle(config)

            self.assertTrue(save_seen_mock.called)

    def test_calculate_viral_score_rewards_multiple_official_sources_and_videos(self):
        grouped_article = {
            "title": "Messi scores a dramatic late winner in a FIFA World Cup qualifier",
            "summary": "The moment is trending across official and broadcaster channels.",
            "sources": ["FIFA", "BBC Sport Football", "ESPN FC", "OneFootball"],
            "links": [
                "https://www.fifa.com/article-1",
                "https://www.bbc.com/sport/article-2",
                "https://www.espn.com/article-3",
            ],
            "video_links": [
                "https://www.youtube.com/watch?v=abc123",
                "https://www.youtube.com/watch?v=def456",
            ],
            "official_source": "FIFA",
        }

        score = calculate_viral_score(grouped_article)

        self.assertGreaterEqual(score, 75)

    def test_discovery_message_includes_summary_links_and_short_scripts(self):
        grouped_article = {
            "title": "Messi makes history with a stunning free kick",
            "summary": "The clip is exploding across official accounts and broadcasters.",
            "sources": ["FIFA", "BBC Sport Football", "ESPN FC"],
            "links": ["https://www.fifa.com/article", "https://www.bbc.com/article"],
            "video_links": ["https://www.youtube.com/watch?v=abc123"],
            "video_url": "https://www.youtube.com/watch?v=abc123",
            "score": 9.6,
            "reason": "The story is trending because it combines a huge star and a dramatic moment.",
        }

        message = build_content_discovery_telegram_message(grouped_article, {})

        self.assertIn("Resumo da história", message)
        self.assertIn("Por que está explodindo", message)
        self.assertIn("Links para todos os artigos", message)
        self.assertIn("Links para todos os vídeos", message)
        self.assertIn("30s", message)
        self.assertIn("45s", message)
        self.assertIn("60s", message)
        self.assertIn("CTA", message)

    def test_discovery_message_uses_structured_messi_alert_layout(self):
        grouped_article = {
            "title": "Messi marca golaço!",
            "summary": "Momento decisivo com reação enorme das redes.",
            "sources": ["FIFA", "ESPN", "BBC", "Reuters"],
            "links": ["https://www.fifa.com/article", "https://www.espn.com/article"],
            "video_links": ["https://www.youtube.com/watch?v=abc123"],
            "video_url": "https://www.youtube.com/watch?v=abc123",
            "score": 9.6,
            "reason": "A story with a huge star and dramatic moment is exploding.",
        }

        message = build_content_discovery_telegram_message(grouped_article, {})

        self.assertIn("🚨 Messi marca golaço!", message)
        self.assertIn("📰 Notícias", message)
        self.assertIn("🎥 Vídeos", message)
        self.assertIn("🔥 Viral Score: 96/100", message)
        self.assertIn("🎙 HeyGen", message)
        self.assertIn("📝 Shorts", message)
        self.assertIn("📸 Thumbnail", message)


if __name__ == "__main__":
    unittest.main()

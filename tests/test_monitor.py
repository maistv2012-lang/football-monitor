import subprocess
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
    download_youtube_video,
    group_articles,
    is_live_goal_event,
    is_trusted_youtube_uploader,
    process_cycle,
    prepare_telegram_message,
    search_and_download_youtube_video,
    select_official_youtube_candidate,
    send_telegram_notification,
    should_send_notification,
)

from monitor import save_seen_articles


class MonitorTests(unittest.TestCase):
    def test_youtube_metadata_timeout_returns_cleanly(self):
        with patch("monitor.subprocess.run", side_effect=subprocess.TimeoutExpired("yt-dlp", 20)) as run_mock, \
             self.assertLogs("football-monitor", level="WARNING") as captured:
            result = search_and_download_youtube_video(
                "Messi goal", {}, set(), preferred_url="https://youtu.be/official001"
            )

        self.assertEqual(result, (None, None))
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 20)
        self.assertIn("Video inspection timed out.", "\n".join(captured.output))

    def test_youtube_download_subprocess_has_timeout(self):
        completed = type("Completed", (), {"stdout": "downloads/video.mp4\n", "stderr": ""})()
        with patch("monitor.subprocess.run", return_value=completed) as run_mock:
            path = download_youtube_video(
                "https://youtu.be/official001", Path("downloads"), "yt-dlp"
            )

        self.assertEqual(path, "downloads/video.mp4")
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 120)

    def test_very_long_telegram_message_is_compacted_before_first_send(self):
        response = type("Response", (), {"status_code": 200, "text": ""})()
        article = {
            "title": "Messi scores the winning goal",
            "score": 9.7,
            "sources": ["FIFA"],
            "links": ["https://example.com/original"],
            "video_url": "https://www.youtube.com/watch?v=official001",
            "shorts_title": "Messi decidiu no último minuto!",
            "is_manual_event": True,
        }

        with patch("monitor.build_manual_telegram_message", return_value="long section " * 1000), \
             patch("monitor.requests.post", return_value=response) as post_mock:
            sent = send_telegram_notification(
                article,
                {"telegram_bot_token": "TEST_TOKEN", "telegram_chat_id": "123"},
            )

        payload = post_mock.call_args.kwargs["json"]["text"]
        self.assertTrue(sent)
        self.assertLess(len(payload), 3500)
        self.assertIn("Messi scores the winning goal", payload)
        self.assertIn("9.7", payload)
        self.assertIn("FIFA", payload)
        self.assertIn("https://example.com/original", payload)
        self.assertIn("https://www.youtube.com/watch?v=official001", payload)
        self.assertIn("Messi decidiu no último minuto!", payload)
        self.assertNotIn("long section long section", payload)

    def test_telegram_400_logs_response_and_truncated_message_without_token(self):
        token = "SECRET_TEST_BOT_TOKEN"
        response = type("Response", (), {
            "status_code": 400,
            "text": '{"ok":false,"error_code":400,"description":"Bad Request: invalid message"}',
        })()
        message = f"alert {token} " + ("x" * 600)

        with patch("monitor.build_manual_telegram_message", return_value=message), \
             patch("monitor.requests.post", return_value=response), \
             self.assertLogs("football-monitor", level="INFO") as captured:
            sent = send_telegram_notification(
                {"title": "Test", "is_manual_event": True},
                {"telegram_bot_token": token, "telegram_chat_id": "123"},
            )

        logs = "\n".join(captured.output)
        self.assertFalse(sent)
        self.assertIn("Telegram 400", logs)
        self.assertIn('"description":"Bad Request: invalid message"', logs)
        self.assertIn("[REDACTED]", logs)
        self.assertNotIn(token, logs)
        self.assertNotIn("x" * 501, logs)

    def test_telegram_transport_failure_returns_false_and_does_not_expose_token(self):
        token = "SECRET_TRANSPORT_TOKEN"
        with patch("monitor.build_manual_telegram_message", return_value="test message"), \
             patch("monitor.requests.post", side_effect=Exception(f"request failed for {token}")), \
             self.assertLogs("football-monitor", level="ERROR") as captured:
            sent = send_telegram_notification(
                {"title": "Test", "is_manual_event": True},
                {"telegram_bot_token": token, "telegram_chat_id": "123"},
            )

        self.assertFalse(sent)
        self.assertNotIn(token, "\n".join(captured.output))

    def test_trusted_youtube_uploader_requires_exact_channel_name(self):
        self.assertTrue(is_trusted_youtube_uploader({"channel": "CazéTV"}))
        self.assertTrue(is_trusted_youtube_uploader({"channel": "FIFA+"}))
        self.assertTrue(is_trusted_youtube_uploader({"uploader": "ESPN FC"}))
        self.assertFalse(is_trusted_youtube_uploader({"channel": "FIFA Fan Clips"}))

    def test_search_selection_skips_untrusted_and_unrelated_videos(self):
        candidates = [
            {"id": "untrusted01", "title": "Messi goal", "channel": "Fan Football"},
            {"id": "unrelated01", "title": "Football gaming compilation", "channel": "FIFA"},
            {"id": "official001", "title": "Messi scores dramatic goal", "channel": "FIFA"},
        ]

        selected = select_official_youtube_candidate(candidates, "Messi dramatic goal", set())

        self.assertEqual(selected["id"], "official001")

    def test_search_selection_ranks_all_results_instead_of_taking_first(self):
        candidates = [
            {"id": "firstresult", "title": "Messi football update", "channel": "FIFA"},
            {"id": "betterresult", "title": "Messi dramatic winning goal Argentina", "channel": "FIFA+"},
        ]

        selected = select_official_youtube_candidate(
            candidates, "Messi dramatic winning goal Argentina", set()
        )

        self.assertEqual(selected["id"], "betterresult")

    def test_official_channel_still_rejects_rap_music_and_parody(self):
        candidates = [
            {"id": "rapbattle01", "title": "Yamal vs Messi Rap Battle Music Video", "channel": "FIFA"},
            {"id": "parodyvid1", "title": "Messi goal parody reaction", "channel": "ESPN FC"},
        ]

        self.assertIsNone(select_official_youtube_candidate(candidates, "Messi goal", set()))

    def test_article_youtube_url_is_preferred_without_searching(self):
        metadata = '{"id":"official001","title":"Messi goal","channel":"FIFA"}'
        completed = type("Completed", (), {"stdout": metadata, "stderr": ""})()
        config = {"yt_dlp_bin": "yt-dlp", "downloads_dir": Path("downloads")}

        with patch("monitor.subprocess.run", return_value=completed) as run_mock, \
             patch("monitor.download_youtube_video", return_value="downloads/video.mp4") as download_mock:
            path, url = search_and_download_youtube_video(
                "Messi goal", config, set(), preferred_url="https://youtu.be/official001"
            )

        self.assertEqual(path, "downloads/video.mp4")
        self.assertEqual(url, "https://youtu.be/official001")
        self.assertNotIn("ytsearch", " ".join(run_mock.call_args.args[0]))
        download_mock.assert_called_once()

    def test_untrusted_article_youtube_url_is_not_downloaded_or_replaced(self):
        metadata = '{"id":"fanvideo001","title":"Messi fan reaction","channel":"Fan Football"}'
        completed = type("Completed", (), {"stdout": metadata, "stderr": ""})()

        with patch("monitor.subprocess.run", return_value=completed) as run_mock, \
             patch("monitor.download_youtube_video") as download_mock:
            result = search_and_download_youtube_video(
                "Messi goal", {}, set(), preferred_url="https://youtu.be/fanvideo001"
            )

        self.assertEqual(result, (None, None))
        self.assertEqual(run_mock.call_count, 1)
        download_mock.assert_not_called()

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

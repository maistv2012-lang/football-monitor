import subprocess
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from monitor import (
    GeoRestrictedVideoError,
    build_article_key,
    build_content_discovery_telegram_message,
    build_live_event_telegram_message,
    build_match_highlight_queries,
    build_manual_grouped_article,
    build_portuguese_shorts_pack,
    build_portuguese_telegram_message,
    build_youtube_feed_url,
    build_x_search_terms,
    build_x_telegram_message,
    calculate_viral_score,
    download_youtube_video,
    discover_x_posts,
    group_articles,
    is_live_goal_event,
    is_trusted_youtube_uploader,
    process_cycle,
    prepare_telegram_message,
    search_and_download_youtube_video,
    select_official_youtube_candidate,
    send_telegram_notification,
    should_send_notification,
    validate_highlight_candidate,
)

from monitor import save_seen_articles


class MonitorTests(unittest.TestCase):
    def test_x_search_terms_include_match_teams_competition_and_player(self):
        terms = build_x_search_terms({
            "title": "Argentina vs Egypt: Messi scores",
            "competition": "FIFA World Cup",
        })
        self.assertIn("Argentina", terms)
        self.assertIn("Egypt", terms)
        self.assertIn("FIFA World Cup", terms)
        self.assertIn("Messi", terms)

    def test_x_discovery_accepts_only_trusted_relevant_accounts_with_metrics(self):
        response = type("Response", (), {
            "status_code": 200,
            "json": lambda self: {
                "data": [
                    {"id": "101", "author_id": "1", "text": "Argentina vs Egypt match update", "public_metrics": {"like_count": 50, "retweet_count": 8, "impression_count": 900}},
                    {"id": "102", "author_id": "2", "text": "Argentina vs Egypt fan post", "public_metrics": {}},
                ],
                "includes": {"users": [
                    {"id": "1", "name": "FIFA World Cup", "username": "FIFAWorldCup"},
                    {"id": "2", "name": "Fan Account", "username": "RandomFan"},
                ]},
            },
        })()
        with patch("monitor.requests.get", return_value=response), \
             patch("monitor.download_youtube_video") as download_mock:
            posts = discover_x_posts(
                {"title": "Argentina vs Egypt", "competition": "FIFA World Cup"},
                {"x_bearer_token": "TEST_X_TOKEN"},
            )

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["url"], "https://x.com/FIFAWorldCup/status/101")
        self.assertEqual(posts[0]["likes"], 50)
        self.assertEqual(posts[0]["reposts"], 8)
        self.assertEqual(posts[0]["views"], 900)
        download_mock.assert_not_called()

    def test_x_discovery_message_contains_post_account_url_and_metrics(self):
        message = build_x_telegram_message({
            "text": "Late winning goal!",
            "account_name": "BBC Sport",
            "url": "https://x.com/BBCSport/status/123",
            "likes": 100,
            "reposts": 20,
            "views": 5000,
        })
        self.assertIn("Late winning goal!", message)
        self.assertIn("BBC Sport", message)
        self.assertIn("https://x.com/BBCSport/status/123", message)
        self.assertIn("Likes: 100", message)
        self.assertIn("Reposts: 20", message)
        self.assertIn("Views: 5000", message)

    def test_x_discovery_without_credentials_continues_without_request(self):
        with patch("monitor.requests.get") as get_mock:
            self.assertEqual(discover_x_posts({"title": "Argentina vs Egypt"}, {}), [])
        get_mock.assert_not_called()

    def test_match_highlight_queries_cover_supported_competitions(self):
        self.assertIn("World Cup 2026", " ".join(build_match_highlight_queries("Portugal", "Spain", "FIFA World Cup")))
        self.assertIn("Brasileirão", " ".join(build_match_highlight_queries("Flamengo", "Palmeiras", "Campeonato Brasileiro")))
        self.assertIn("Champions League", " ".join(build_match_highlight_queries("Real Madrid", "Manchester City", "Champions League")))

    def test_highlights_discovery_accepts_valid_non_official_video(self):
        valid, reason = validate_highlight_candidate(
            {"title": "Portugal vs Spain highlights World Cup 2026", "uploader": "NZ Football Coverage", "duration": 420, "upload_date": "20260706"},
            ("Portugal", "Spain"), datetime(2026, 7, 7, tzinfo=timezone.utc),
        )
        self.assertTrue(valid, reason)

    def test_highlights_discovery_rejects_reaction_video(self):
        valid, _ = validate_highlight_candidate(
            {"title": "Portugal vs Spain highlights reaction", "uploader": "Football Talk", "duration": 300, "upload_date": "20260706"},
            ("Portugal", "Spain"), datetime(2026, 7, 7, tzinfo=timezone.utc),
        )
        self.assertFalse(valid)

    def test_highlights_discovery_rejects_full_match(self):
        for title in ("Portugal vs Spain highlights full match", "Portugal vs Spain melhores momentos jogo completo"):
            with self.subTest(title=title):
                valid, _ = validate_highlight_candidate(
                    {"title": title, "uploader": "Football Coverage", "duration": 600, "upload_date": "20260706"},
                    ("Portugal", "Spain"), datetime(2026, 7, 7, tzinfo=timezone.utc),
                )
                self.assertFalse(valid)

    def test_highlights_discovery_rejects_video_missing_one_team(self):
        valid, _ = validate_highlight_candidate(
            {"title": "Portugal highlights World Cup 2026", "uploader": "Football Coverage", "duration": 300, "upload_date": "20260706"},
            ("Portugal", "Spain"), datetime(2026, 7, 7, tzinfo=timezone.utc),
        )
        self.assertFalse(valid)

    def test_highlights_discovery_rejects_old_video(self):
        valid, _ = validate_highlight_candidate(
            {"title": "Portugal vs Spain highlights", "uploader": "Football Coverage", "duration": 300, "upload_date": "20260101"},
            ("Portugal", "Spain"), datetime(2026, 7, 7, tzinfo=timezone.utc),
        )
        self.assertFalse(valid)
    def test_youtube_metadata_timeout_returns_cleanly(self):
        search_result = type("Completed", (), {
            "stdout": '{"id":"official001","title":"Messi goal","channel":"FIFA","webpage_url":"https://youtube.com/watch?v=official001"}',
            "stderr": "",
        })()
        with patch(
            "monitor.subprocess.run",
            side_effect=[subprocess.TimeoutExpired("yt-dlp", 20), search_result],
        ) as run_mock, \
             patch("monitor.download_youtube_video", return_value="downloads/official.mp4"), \
             self.assertLogs("football-monitor", level="WARNING") as captured:
            result = search_and_download_youtube_video(
                "Messi goal", {}, set(), preferred_url="https://youtu.be/official001"
            )

        self.assertEqual(
            result,
            ("downloads/official.mp4", "https://youtube.com/watch?v=official001"),
        )
        self.assertEqual(run_mock.call_count, 2)
        self.assertTrue(all(call.kwargs["timeout"] == 20 for call in run_mock.call_args_list))
        self.assertIn(
            "Article video inspection timed out. Falling back to YouTube search.",
            "\n".join(captured.output),
        )

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
        for channel_name in ("CazéTV", "CazeTV", "Cazé TV", "Caze TV", "@CazeTV", "⚽ CazéTV™"):
            with self.subTest(channel_name=channel_name):
                self.assertTrue(is_trusted_youtube_uploader({"channel": channel_name}))
        self.assertTrue(is_trusted_youtube_uploader({"channel": "FIFA+"}))
        self.assertTrue(is_trusted_youtube_uploader({"uploader": "ESPN FC"}))
        self.assertFalse(is_trusted_youtube_uploader({"channel": "FIFA Fan Clips"}))

    def test_new_zealand_uploaders_are_trusted(self):
        self.assertTrue(is_trusted_youtube_uploader({"channel": "TVNZ"}))
        self.assertTrue(is_trusted_youtube_uploader({"channel": "TVNZ+"}))
        self.assertTrue(is_trusted_youtube_uploader({"uploader": "Sky Sport NZ"}))

    def test_geo_blocked_cazetv_uses_requested_fallback_order(self):
        search_output = "\n".join([
            '{"id":"fifavideo01","title":"CR7 World Cup goals","channel":"FIFA","webpage_url":"https://youtube.com/watch?v=fifavideo01"}',
            '{"id":"tvnzvideo01","title":"CR7 World Cup goals","channel":"TVNZ","webpage_url":"https://youtube.com/watch?v=tvnzvideo01"}',
        ])
        completed = type("Completed", (), {"stdout": search_output, "stderr": ""})()

        with patch("monitor.subprocess.run", return_value=completed), \
             patch("monitor.download_youtube_video", return_value="downloads/fifa.mp4") as download_mock:
            result = search_and_download_youtube_video(
                "CR7 World Cup goals",
                {},
                set(),
                preferred_url="https://youtube.com/watch?v=cazevideo01",
                trusted_source="CazéTV",
            )

        self.assertEqual(
            result,
            ("downloads/fifa.mp4", "https://youtube.com/watch?v=fifavideo01"),
        )
        self.assertEqual(download_mock.call_count, 1)
        self.assertEqual(download_mock.call_args.args[0], "https://youtube.com/watch?v=fifavideo01")
        self.assertNotIn("cazevideo01", str(download_mock.call_args))

    def test_cazetv_source_skips_original_video_and_searches_other_sources(self):
        url = "https://www.youtube.com/watch?v=official001"
        search_result = type("Completed", (), {
            "stdout": '{"id":"fifavideo01","title":"GOLEADA World Cup match highlights","channel":"FIFA","webpage_url":"https://youtube.com/watch?v=fifavideo01"}',
            "stderr": "",
        })()
        with patch("monitor.subprocess.run", return_value=search_result) as search_mock, \
             patch("monitor.download_youtube_video", return_value="downloads/fifa.mp4") as download_mock:
            result = search_and_download_youtube_video(
                "GOLEADA NOS ANFITRIÕES E DESPEDIDA DO CR7",
                {},
                set(),
                preferred_url=url,
                trusted_source="Cazé TV",
            )

        self.assertEqual(result, ("downloads/fifa.mp4", "https://youtube.com/watch?v=fifavideo01"))
        self.assertNotIn(url, " ".join(search_mock.call_args.args[0]))
        self.assertNotIn(url, str(download_mock.call_args))

    def test_cazetv_source_still_rejects_forbidden_content(self):
        empty_result = type("Completed", (), {"stdout": "", "stderr": ""})()
        with patch("monitor.subprocess.run", return_value=empty_result), \
             patch("monitor.download_youtube_video") as download_mock:
            result = search_and_download_youtube_video(
                "Yamal vs Messi rap battle reaction",
                {},
                set(),
                preferred_url="https://www.youtube.com/watch?v=fanvideo001",
                trusted_source="@CazeTV",
            )

        self.assertEqual(result, (None, None))
        download_mock.assert_not_called()

    def test_cazetv_news_only_falls_back_to_non_official_highlights(self):
        highlight = type("Completed", (), {
            "stdout": (
                '{"id":"highlight01","title":"Portugal vs Spain highlights World Cup 2026",'
                '"uploader":"NZ Match Coverage","duration":420,"upload_date":"20260707",'
                '"webpage_url":"https://youtube.com/watch?v=highlight01"}'
            ),
            "stderr": "",
        })()
        with patch("monitor.subprocess.run", return_value=highlight), \
             patch("monitor.download_youtube_video", return_value="downloads/highlight.mp4"), \
             self.assertLogs("football-monitor", level="INFO") as captured:
            result = search_and_download_youtube_video(
                "Portugal vs Spain highlights World Cup 2026",
                {},
                set(),
                preferred_url="https://youtube.com/watch?v=cazevideo01",
                trusted_source="CazéTV",
            )

        self.assertEqual(
            result,
            ("downloads/highlight.mp4", "https://youtube.com/watch?v=highlight01"),
        )
        logs = "\n".join(captured.output)
        self.assertIn("Searching non-official match highlights...", logs)
        self.assertIn("Highlight candidate accepted", logs)

    def test_youtube_candidate_diagnostics_include_title_uploader_and_url(self):
        candidate = {
            "id": "official001",
            "title": "Messi winning goal",
            "channel": "FIFA",
            "webpage_url": "https://www.youtube.com/watch?v=official001",
        }
        with self.assertLogs("football-monitor", level="INFO") as captured:
            select_official_youtube_candidate([candidate], "Messi winning goal", set())

        logs = "\n".join(captured.output)
        self.assertIn("Messi winning goal", logs)
        self.assertIn("FIFA", logs)
        self.assertIn("https://www.youtube.com/watch?v=official001", logs)

    def test_search_selection_skips_untrusted_and_unrelated_videos(self):
        candidates = [
            {"id": "untrusted01", "title": "Messi goal", "channel": "Fan Football"},
            {"id": "unrelated01", "title": "Football gaming compilation", "channel": "FIFA"},
            {"id": "official001", "title": "Messi scores dramatic goal", "channel": "FIFA"},
        ]

        selected = select_official_youtube_candidate(candidates, "Messi dramatic goal", set())

        self.assertEqual(selected["id"], "official001")

    def test_search_selection_uses_official_channel_priority(self):
        candidates = [
            {"id": "firstresult", "title": "Messi football update", "channel": "FIFA"},
            {"id": "betterresult", "title": "Messi dramatic winning goal Argentina", "channel": "FIFA+"},
        ]

        selected = select_official_youtube_candidate(
            candidates, "Messi dramatic winning goal Argentina", set()
        )

        self.assertEqual(selected["id"], "firstresult")

    def test_geo_restricted_candidate_falls_back_to_next_official_upload(self):
        search_output = "\n".join([
            '{"id":"fifavideo01","title":"Messi winning goal","channel":"FIFA","webpage_url":"https://youtube.com/watch?v=fifavideo01"}',
            '{"id":"espnvideo01","title":"Messi winning goal","channel":"ESPN FC","webpage_url":"https://youtube.com/watch?v=espnvideo01"}',
        ])
        completed = type("Completed", (), {"stdout": search_output, "stderr": ""})()

        with patch("monitor.subprocess.run", return_value=completed), \
             patch(
                 "monitor.download_youtube_video",
                 side_effect=[GeoRestrictedVideoError(), "downloads/espn.mp4"],
             ) as download_mock, \
             self.assertLogs("football-monitor", level="INFO") as captured:
            result = search_and_download_youtube_video("Messi winning goal", {}, set())

        self.assertEqual(
            result,
            ("downloads/espn.mp4", "https://youtube.com/watch?v=espnvideo01"),
        )
        self.assertEqual(download_mock.call_count, 2)
        logs = "\n".join(captured.output)
        self.assertIn("Official download is geo-restricted.", logs)
        self.assertIn("Trying next trusted official channel...", logs)

    def test_available_in_brazil_download_error_is_geo_restriction(self):
        error = subprocess.CalledProcessError(
            1,
            ["yt-dlp"],
            stderr="ERROR: This video is available in Brazil.",
        )
        with patch("monitor.subprocess.run", side_effect=error):
            with self.assertRaises(GeoRestrictedVideoError):
                download_youtube_video(
                    "https://youtube.com/watch?v=cazevideo01", Path("downloads"), "yt-dlp"
                )

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
        self.assertEqual(run_mock.call_count, 2)
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

import json
import os
import subprocess
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import ANY, patch

from monitor import (
    GeoRestrictedVideoError,
    _format_drawtext_font_path,
    attach_article_to_match,
    build_article_key,
    build_content_discovery_telegram_message,
    build_live_event_telegram_message,
    build_match_highlight_queries,
    build_match_day_queries,
    build_manual_grouped_article,
    build_short_metadata,
    build_portuguese_shorts_pack,
    build_portuguese_telegram_message,
    build_youtube_feed_url,
    build_x_search_terms,
    build_x_telegram_message,
    calculate_viral_score,
    classify_story_content,
    create_best_moments_clip,
    create_vertical_short,
    detect_audio_peak_timestamps,
    discover_tvnz_sport_videos,
    discover_tvnz_rss_videos,
    download_new_tvnz_sport_highlights,
    download_youtube_video,
    discover_x_posts,
    find_short_font_path,
    group_articles,
    is_cazetv_discussion_content,
    is_download_eligible_title,
    is_live_goal_event,
    is_tvnz_highlight_video,
    is_trusted_youtube_uploader,
    load_todays_fixtures,
    parse_tvnz_max_downloads_per_run,
    parse_tvnz_rss_entries,
    process_cycle,
    prepare_telegram_message,
    search_and_download_youtube_video,
    select_official_youtube_candidate,
    send_downloaded_video_to_telegram,
    send_telegram_notification,
    should_send_notification,
    select_best_moment_segments,
    validate_highlight_candidate,
    validate_youtube_download_candidate,
)

from monitor import save_seen_articles


class MonitorTests(unittest.TestCase):
    def test_classify_story_content_download_categories(self):
        cases = (
            ("Portugal vs Spain match highlights", "MATCH_HIGHLIGHT"),
            ("Messi scores dramatic goal", "GOAL_CLIP"),
            ("VAR awards late penalty to Argentina", "VAR_OR_PENALTY"),
            ("Red card drama in Brazil vs Norway", "RED_CARD"),
            ("Penalty shootout: Switzerland v Colombia", "SHOOTOUT"),
        )
        for title, category in cases:
            with self.subTest(title=title):
                decision = classify_story_content(title, "BBC Sport")
                self.assertEqual(decision["category"], category)
                self.assertTrue(decision["should_alert"])
                self.assertTrue(decision["should_download"])

    def test_classify_story_content_blocks_discussion_and_live_downloads(self):
        cases = (
            ("GERAL CAZÉTV debate da rodada", "DISCUSSION"),
            ("AQUI É COPA reacts ao jogo", "DISCUSSION"),
            ("AO VIVO live da madrugada", "LIVE_STREAM"),
            ("Transfer news: striker signs", "TRANSFER_NEWS"),
            ("World Cup schedule update", "GENERAL_NEWS"),
            ("", "UNKNOWN"),
        )
        for title, category in cases:
            with self.subTest(title=title):
                decision = classify_story_content(title, "CazéTV")
                self.assertEqual(decision["category"], category)
                self.assertFalse(decision["should_download"])

    def test_cazetv_discussion_filter_is_source_specific(self):
        ignored_terms = (
            "GERAL CAZÉTV", "AQUI É COPA", "AO VIVO", "LIVE DA MADRUGADA",
            "DEBATE", "OPINIÃO", "REAGE", "PODCAST",
        )
        for term in ignored_terms:
            with self.subTest(term=term):
                self.assertTrue(is_cazetv_discussion_content("CazéTV", f"{term}: gol da rodada"))
                self.assertFalse(is_cazetv_discussion_content("BBC Sport", f"{term}: gol da rodada"))

        accepted_titles = (
            "GOL", "GOLAÇO", "PÊNALTI", "PENALTY", "VAR", "CARTÃO",
            "RED CARD", "DEFESA", "SAVE", "MELHORES MOMENTOS", "HIGHLIGHTS",
            "RESUMO DO DIA", "TODOS OS GOLS",
        )
        for title in accepted_titles:
            with self.subTest(title=title):
                self.assertFalse(is_cazetv_discussion_content("CazéTV", title))

    def test_download_eligibility_requires_explicit_football_event_title(self):
        accepted = (
            "Portugal vs Spain match highlights", "Melhores momentos Portugal x Espanha",
            "Brazil vs Norway resumo do jogo", "Portugal x Spain todos os gols",
            "Argentina score two incredible goals", "Penalty shootout: Switzerland v Colombia",
            "Messi goal", "Gol de falta do Brasil", "VAR decision for Argentina",
            "Red card incident in Brazil vs Norway", "Brazil brilliant save",
        )
        for title in accepted:
            with self.subTest(title=title):
                self.assertTrue(is_download_eligible_title(title))

        rejected = (
            "Geral CazéTV highlights", "Live da madrugada highlights",
            "Jogo ao vivo highlights", "Aqui é Copa goal", "Debate: World Cup goal",
            "Análise do gol", "Opinion: best goal", "Match preview highlights",
            "Football podcast goal", "Coach reacts to goal", "Reação ao gol",
            "Programa de futebol highlights", "World Cup tracker goals",
            "Golden boot race goals", "Monday musings: highlights",
            "World Cup discussion and news",
        )
        for title in rejected:
            with self.subTest(title=title):
                self.assertFalse(is_download_eligible_title(title))

    def test_generic_listicle_articles_alert_but_do_not_download(self):
        titles = (
            "Late goals, comebacks and upsets - is record-breaking World Cup best ever?",
            "A Golden Boot race for the ages - but who will come out on top?",
            "How to take a World Cup shootout penalty",
            "Goals galore - how dominant is Premier League wealth at World Cup?",
            "Best Group Stage Goals | FIFA World Cup 2026",
            "OS 5 GOLS MAIS BONITOS",
            "top goals",
            "best goals",
            "melhores gols",
            "gols mais bonitos",
            "best moments",
            "iconic moments",
        )
        for title in titles:
            with self.subTest(title=title):
                decision = classify_story_content(title, "FIFA")
                self.assertEqual(decision["category"], "GENERAL_NEWS")
                self.assertTrue(decision["should_alert"])
                self.assertFalse(decision["should_download"])

    def test_transfer_titles_are_not_downloadable(self):
        titles = (
            "Star signs for Real Madrid",
            "Forward joins Premier League club",
            "Midfielder agrees deal with Barcelona",
            "World Cup winner transfer latest",
            "Young striker loan confirmed",
            "Defender set to sign tomorrow",
            "Goalkeeper close to signing",
        )
        for title in titles:
            with self.subTest(title=title):
                decision = classify_story_content(title, "BBC Sport")
                self.assertEqual(decision["category"], "TRANSFER_NEWS")
                self.assertTrue(decision["should_alert"])
                self.assertFalse(decision["should_download"])

    def test_specific_match_or_event_titles_can_download(self):
        titles = (
            "Argentina vs Egypt Match Highlights",
            "France v Belgium Extended Highlights",
            "Messi scores winner against Egypt",
            "Penalty shootout: Switzerland v Colombia",
            "Red card drama in Brazil vs Norway",
        )
        for title in titles:
            with self.subTest(title=title):
                decision = classify_story_content(title, "BBC Sport")
                self.assertTrue(decision["should_download"])

    def test_generic_article_does_not_start_youtube_search(self):
        with patch("monitor.subprocess.run") as run_mock, \
             patch("monitor.download_youtube_video") as download_mock:
            result = search_and_download_youtube_video(
                "World Cup discussion and commentary", {}, set()
            )

        self.assertEqual(result, (None, None))
        run_mock.assert_not_called()
        download_mock.assert_not_called()

    def test_generic_goal_article_does_not_start_youtube_search(self):
        with patch("monitor.subprocess.run") as run_mock, \
             patch("monitor.download_youtube_video") as download_mock:
            result = search_and_download_youtube_video(
                "Late goals, comebacks and upsets - is record-breaking World Cup best ever?",
                {},
                set(),
            )

        self.assertEqual(result, (None, None))
        run_mock.assert_not_called()
        download_mock.assert_not_called()

    def test_match_day_structure_attaches_article_when_one_team_is_mentioned(self):
        match = {
            "home_team": "Argentina",
            "away_team": "Egypt",
            "competition": "FIFA World Cup 2026",
            "kickoff_time": "20:00",
            "status": "scheduled",
        }
        article = attach_article_to_match(
            {"title": "Argentina prepares for tonight's match", "summary": ""}, [match]
        )
        self.assertEqual(article["match"], match)

    def test_match_day_queries_use_both_teams_before_article_title(self):
        queries = build_match_day_queries({
            "home_team": "Argentina",
            "away_team": "Egypt",
            "competition": "FIFA World Cup 2026",
            "kickoff_time": "20:00",
            "status": "scheduled",
        })
        self.assertEqual(queries[0], "Argentina vs Egypt highlights FIFA World Cup 2026")
        self.assertIn("Argentina Egypt match highlights", queries)
        self.assertIn("Argentina Egypt melhores momentos", queries)
        self.assertIn("Argentina x Egypt melhores momentos", queries)

    def test_article_uses_best_match_from_entire_daily_list(self):
        matches = [
            {"home_team": "Argentina", "away_team": "Egypt", "competition": "World Cup", "kickoff_time": "18:00", "status": "scheduled"},
            {"home_team": "Switzerland", "away_team": "Colombia", "competition": "World Cup", "kickoff_time": "21:00", "status": "scheduled"},
        ]
        article = attach_article_to_match(
            {"title": "Switzerland vs Colombia: match preview", "summary": "Argentina also plays today"},
            matches,
        )
        self.assertEqual(article["match"]["home_team"], "Switzerland")
        self.assertEqual(article["match"]["away_team"], "Colombia")

    def test_article_without_match_remains_unattached(self):
        article = {"title": "Unrelated transfer story", "summary": ""}
        self.assertNotIn("match", attach_article_to_match(article, [{
            "home_team": "Argentina", "away_team": "Egypt", "competition": "FIFA World Cup 2026",
            "kickoff_time": "20:00", "status": "scheduled",
        }]))

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
            "stdout": '{"id":"official001","title":"Messi match highlights","channel":"TVNZ Sport","webpage_url":"https://youtube.com/watch?v=official001"}',
            "stderr": "",
        })()
        with patch(
            "monitor.subprocess.run",
            side_effect=[subprocess.TimeoutExpired("yt-dlp", 20), search_result],
        ) as run_mock, \
             patch("monitor.download_youtube_video", return_value="downloads/official.mp4"), \
             self.assertLogs("football-monitor", level="WARNING") as captured:
            result = search_and_download_youtube_video(
                "Messi highlights", {}, set(), preferred_url="https://youtu.be/official001"
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
        with TemporaryDirectory() as temp_dir:
            downloads_dir = Path(temp_dir) / "downloads"
            downloads_dir.mkdir()
            video_path = downloads_dir / "video.mp4"
            video_path.write_bytes(b"mp4")
            completed = type("Completed", (), {"stdout": f"{video_path}\n", "stderr": ""})()

            with patch("monitor.subprocess.run", return_value=completed) as run_mock:
                path = download_youtube_video(
                    "https://youtu.be/official001", downloads_dir, "yt-dlp"
                )

        self.assertEqual(path, str(video_path))
        self.assertEqual(run_mock.call_args.kwargs["timeout"], 120)

    def test_downloaded_video_resolves_file_by_video_id_when_stdout_path_is_corrupted(self):
        with TemporaryDirectory() as temp_dir:
            downloads_dir = Path(temp_dir) / "downloads"
            downloads_dir.mkdir()
            local_video = downloads_dir / "Portugal-v-Spain-tvnzvideo01.mp4"
            local_video.write_bytes(b"mp4")
            corrupted_stdout = (
                r"C:\Users\MANUJ\OneDrive\�rea de Trabalho\Football-monitor\downloads"
                r"\Portugal-v-Spain-tvnzvideo01.mp4"
            )
            completed = type("Completed", (), {"stdout": f"{corrupted_stdout}\n", "stderr": ""})()

            with patch("monitor.subprocess.run", return_value=completed):
                path = download_youtube_video(
                    "https://youtube.com/watch?v=tvnzvideo01", downloads_dir, "yt-dlp"
                )

        self.assertEqual(path, str(local_video))

    def test_downloaded_video_uses_existing_stdout_path_when_no_video_id_match(self):
        with TemporaryDirectory() as temp_dir:
            downloads_dir = Path(temp_dir) / "downloads"
            downloads_dir.mkdir()
            stdout_video = downloads_dir / "plain-video.mp4"
            stdout_video.write_bytes(b"mp4")
            completed = type("Completed", (), {"stdout": f"{stdout_video}\n", "stderr": ""})()

            with patch("monitor.subprocess.run", return_value=completed):
                path = download_youtube_video(
                    "https://youtube.com/watch?v=missingid01", downloads_dir, "yt-dlp"
                )

        self.assertEqual(path, str(stdout_video))

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

    def test_downloaded_video_sends_with_sendvideo(self):
        response = type("Response", (), {"status_code": 200, "text": '{"ok":true}'})()
        with TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "clip.mp4"
            video_path.write_bytes(b"mp4")
            story = {
                "title": "Messi Argentina vs Egypt Match Highlights",
                "sources": ["TVNZ Sport"],
                "content_category": "MATCH_HIGHLIGHT",
                "links": ["https://example.com/story"],
                "vertical_short_path": "shorts/clip_vertical.mp4",
                "short_metadata_path": "shorts/clip_vertical.txt",
                "moments_duration_seconds": 44.0,
                "_telegram_config": {"telegram_bot_token": "TEST_TOKEN", "telegram_chat_id": "123"},
            }

            with patch("monitor.requests.post", return_value=response) as post_mock:
                sent = send_downloaded_video_to_telegram(video_path, story)

        self.assertTrue(sent)
        self.assertIn("/sendVideo", post_mock.call_args.args[0])
        self.assertIn("Argentina vs Egypt Match Highlights", post_mock.call_args.kwargs["data"]["caption"])
        self.assertIn("TVNZ Sport", post_mock.call_args.kwargs["data"]["caption"])
        self.assertIn("MATCH_HIGHLIGHT", post_mock.call_args.kwargs["data"]["caption"])
        self.assertIn("clip_vertical.mp4", post_mock.call_args.kwargs["data"]["caption"])
        self.assertIn("Duration: 44.0s", post_mock.call_args.kwargs["data"]["caption"])
        self.assertIn("File size:", post_mock.call_args.kwargs["data"]["caption"])
        self.assertIn("clip_vertical.txt", post_mock.call_args.kwargs["data"]["caption"])
        self.assertIn("https://example.com/story", post_mock.call_args.kwargs["data"]["caption"])

    def test_downloaded_video_falls_back_to_senddocument(self):
        fail_response = type("Response", (), {"status_code": 400, "text": '{"ok":false,"description":"Bad Request"}'})()
        ok_response = type("Response", (), {"status_code": 200, "text": '{"ok":true}'})()
        with TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "clip.mp4"
            video_path.write_bytes(b"mp4")
            story = {
                "title": "Messi scores winner against Egypt",
                "sources": ["TVNZ Sport"],
                "content_category": "GOAL_CLIP",
                "_telegram_config": {"telegram_bot_token": "TEST_TOKEN", "telegram_chat_id": "123"},
            }

            with patch("monitor.requests.post", side_effect=[fail_response, ok_response]) as post_mock:
                sent = send_downloaded_video_to_telegram(video_path, story)

        self.assertTrue(sent)
        self.assertIn("/sendVideo", post_mock.call_args_list[0].args[0])
        self.assertIn("/sendDocument", post_mock.call_args_list[1].args[0])

    def test_oversized_downloaded_video_sends_text_notice(self):
        response = type("Response", (), {"status_code": 200, "text": '{"ok":true}'})()
        with TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "large.mp4"
            video_path.write_bytes(b"mp4")
            story = {
                "title": "France v Belgium Extended Highlights",
                "_telegram_config": {"telegram_bot_token": "TEST_TOKEN", "telegram_chat_id": "123"},
            }
            fake_stat = type("Stat", (), {"st_size": 46 * 1024 * 1024})()

            with patch("monitor.Path.stat", return_value=fake_stat), \
                 patch("monitor.requests.post", return_value=response) as post_mock:
                sent = send_downloaded_video_to_telegram(video_path, story)

        self.assertTrue(sent)
        self.assertIn("/sendMessage", post_mock.call_args.args[0])
        payload = post_mock.call_args.kwargs["json"]["text"]
        self.assertIn("Vídeo baixado no PC, mas muito grande para enviar pelo Telegram.", payload)
        self.assertIn(str(video_path), payload)

    def test_create_vertical_short_builds_ffmpeg_command_with_drawtext_when_font_exists(self):
        with TemporaryDirectory() as temp_dir:
            old_cwd = Path.cwd()
            os.chdir(temp_dir)
            try:
                video_path = Path("downloads") / "clip.mp4"
                video_path.parent.mkdir()
                video_path.write_bytes(b"mp4")
                font_path = Path("fonts") / "DejaVuSans-Bold.ttf"
                font_path.parent.mkdir()
                font_path.write_bytes(b"font")

                def fake_run(command, **kwargs):
                    Path(command[-1]).write_bytes(b"short")
                    return type("Result", (), {"stderr": ""})()

                with patch("monitor.find_short_font_path", return_value=font_path), \
                     patch("monitor.subprocess.run", side_effect=fake_run) as run_mock:
                    output = create_vertical_short(
                        video_path,
                        {"title": "Argentina vs Egypt Match Highlights that needs shortening for a polished short"},
                    )
                metadata_exists = (Path("shorts") / "clip_vertical.txt").exists()
            finally:
                os.chdir(old_cwd)

        self.assertEqual(output, str(Path("shorts") / "clip_vertical.mp4"))
        self.assertTrue(metadata_exists)
        command = run_mock.call_args.args[0]
        command_text = " ".join(command)
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("1080:1920", command_text)
        self.assertIn("boxblur", command_text)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", command_text)
        self.assertIn("drawtext", command_text)
        self.assertIn("COMENTA AI", command_text)
        self.assertIn("Futeba & Juninho", command_text)
        self.assertIn("libx264", command)
        self.assertIn("veryfast", command)
        self.assertIn("28", command)
        self.assertIn("aac", command)
        self.assertIn("128k", command)
        self.assertIn("+faststart", command)

    def test_create_vertical_short_falls_back_without_text_when_drawtext_fails(self):
        with TemporaryDirectory() as temp_dir:
            old_cwd = Path.cwd()
            os.chdir(temp_dir)
            try:
                video_path = Path("downloads") / "clip.mp4"
                video_path.parent.mkdir()
                video_path.write_bytes(b"mp4")
                font_path = Path(temp_dir) / "DejaVuSans-Bold.ttf"
                font_path.write_bytes(b"font")

                def fake_run(command, **kwargs):
                    if "drawtext" in " ".join(command):
                        raise subprocess.CalledProcessError(1, command, stderr="drawtext failed")
                    Path(command[-1]).write_bytes(b"short")
                    return type("Result", (), {"stderr": ""})()

                with patch("monitor.find_short_font_path", return_value=font_path), \
                     patch("monitor.subprocess.run", side_effect=fake_run) as run_mock:
                    output = create_vertical_short(video_path, {"title": "Messi scores winner"})
            finally:
                os.chdir(old_cwd)

        self.assertEqual(output, str(Path("shorts") / "clip_vertical.mp4"))
        self.assertEqual(run_mock.call_count, 2)
        self.assertIn("drawtext", " ".join(run_mock.call_args_list[0].args[0]))
        self.assertNotIn("drawtext", " ".join(run_mock.call_args_list[1].args[0]))

    def test_create_vertical_short_skips_drawtext_when_font_missing(self):
        with TemporaryDirectory() as temp_dir:
            old_cwd = Path.cwd()
            os.chdir(temp_dir)
            try:
                video_path = Path("downloads") / "clip.mp4"
                video_path.parent.mkdir()
                video_path.write_bytes(b"mp4")

                def fake_run(command, **kwargs):
                    Path(command[-1]).write_bytes(b"short")
                    return type("Result", (), {"stderr": ""})()

                with patch("monitor.find_short_font_path", return_value=None), \
                     patch("monitor.subprocess.run", side_effect=fake_run) as run_mock:
                    output = create_vertical_short(video_path, {"title": "Messi scores winner"})
            finally:
                os.chdir(old_cwd)

        self.assertEqual(output, str(Path("shorts") / "clip_vertical.mp4"))
        self.assertNotIn("drawtext", " ".join(run_mock.call_args.args[0]))

    def test_create_vertical_short_accepts_windows_font_path(self):
        with TemporaryDirectory() as temp_dir:
            old_cwd = Path.cwd()
            os.chdir(temp_dir)
            try:
                video_path = Path("downloads") / "clip.mp4"
                video_path.parent.mkdir()
                video_path.write_bytes(b"mp4")

                def fake_run(command, **kwargs):
                    Path(command[-1]).write_bytes(b"short")
                    return type("Result", (), {"stderr": ""})()

                with patch("monitor.find_short_font_path", return_value=Path("C:\\Windows\\Fonts\\Arial.ttf")), \
                     patch("monitor.subprocess.run", side_effect=fake_run) as run_mock:
                    output = create_vertical_short(video_path, {"title": "Messi scores winner"})
            finally:
                os.chdir(old_cwd)

        self.assertEqual(output, str(Path("shorts") / "clip_vertical.mp4"))
        command_text = " ".join(run_mock.call_args.args[0])
        self.assertIn("drawtext", command_text)
        self.assertIn("fontfile='C\\:/Windows/Fonts/Arial.ttf'", command_text)

    def test_format_drawtext_font_path_escapes_windows_drive_colon(self):
        self.assertEqual(
            _format_drawtext_font_path("C:/Windows/Fonts/arialbd.ttf"),
            r"C\:/Windows/Fonts/arialbd.ttf",
        )

    def test_format_drawtext_font_path_keeps_linux_path_unchanged(self):
        self.assertEqual(
            _format_drawtext_font_path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        )

    def test_find_short_font_path_accepts_linux_candidate(self):
        with TemporaryDirectory() as temp_dir:
            font_path = Path(temp_dir) / "DejaVuSans-Bold.ttf"
            font_path.write_bytes(b"font")

            with patch.dict(os.environ, {}, clear=True), \
                 patch("monitor.DEFAULT_SHORTS_FONT_PATHS", (str(font_path),)):
                self.assertEqual(find_short_font_path(), font_path)

    def test_find_short_font_path_uses_env_before_defaults(self):
        with TemporaryDirectory() as temp_dir:
            env_font_path = Path(temp_dir) / "env-font.ttf"
            default_font_path = Path(temp_dir) / "default-font.ttf"
            env_font_path.write_bytes(b"font")
            default_font_path.write_bytes(b"font")

            with patch.dict(os.environ, {"SHORTS_FONT_PATH": str(env_font_path)}, clear=True), \
                 patch("monitor.DEFAULT_SHORTS_FONT_PATHS", (str(default_font_path),)):
                self.assertEqual(find_short_font_path(), env_font_path)

    def test_build_short_metadata_returns_expected_fields(self):
        metadata = build_short_metadata(
            {
                "title": "Argentina vs Egypt dramatic late winner match highlights",
                "sources": ["TVNZ Sport"],
                "content_category": "GOAL_CLIP",
                "hashtags": ["#Futebol", "#ShortsFutebol"],
                "video_url": "https://youtube.com/watch?v=tvnz1",
            },
            Path("shorts") / "clip_vertical.mp4",
        )

        self.assertIn("title", metadata)
        self.assertIn("description", metadata)
        self.assertIn("hashtags", metadata)
        self.assertIn("pinned_comment", metadata)
        self.assertIn("TVNZ Sport", metadata["description"])
        self.assertIn("https://youtube.com/watch?v=tvnz1", metadata["description"])
        self.assertIn("#WorldCup", metadata["hashtags"])
        self.assertIn("#Futeba", metadata["hashtags"])
        self.assertIn("#Argentina", metadata["hashtags"])
        self.assertIn("#Egypt", metadata["hashtags"])

    def test_short_metadata_txt_includes_youtube_fields(self):
        with TemporaryDirectory() as temp_dir:
            old_cwd = Path.cwd()
            os.chdir(temp_dir)
            try:
                video_path = Path("downloads") / "clip.mp4"
                video_path.parent.mkdir()
                video_path.write_bytes(b"mp4")

                def fake_run(command, **kwargs):
                    Path(command[-1]).write_bytes(b"short")
                    return type("Result", (), {"stderr": ""})()

                story = {
                    "title": "Argentina vs Egypt match highlights",
                    "sources": ["TVNZ Sport"],
                    "video_url": "https://youtube.com/watch?v=tvnz1",
                }
                with patch("monitor.find_short_font_path", return_value=None), \
                     patch("monitor.subprocess.run", side_effect=fake_run):
                    output = create_vertical_short(video_path, story)
                metadata_text = Path("shorts/clip_vertical.txt").read_text(encoding="utf-8")
            finally:
                os.chdir(old_cwd)

        self.assertEqual(output, str(Path("shorts") / "clip_vertical.mp4"))
        self.assertIn("Title:", metadata_text)
        self.assertIn("Description:", metadata_text)
        self.assertIn("Hashtags:", metadata_text)
        self.assertIn("Pinned comment:", metadata_text)
        self.assertIn("clip_vertical.txt", story["short_metadata_path"])

    def test_create_best_moments_clip_long_video_creates_moments_file(self):
        with TemporaryDirectory() as temp_dir:
            old_cwd = Path.cwd()
            os.chdir(temp_dir)
            try:
                video_path = Path("downloads") / "tvnz.mp4"
                video_path.parent.mkdir()
                video_path.write_bytes(b"mp4")

                def fake_run(command, **kwargs):
                    if command[0] == "ffprobe":
                        return type("Result", (), {"stdout": "120.0\n", "stderr": ""})()
                    if command[0] == "ffmpeg" and "astats" in " ".join(command):
                        return type("Result", (), {"stdout": "", "stderr": ""})()
                    if command[0] == "ffmpeg" and "select=gt" in " ".join(command):
                        return type("Result", (), {"stdout": "", "stderr": ""})()
                    Path(command[-1]).write_bytes(b"moments")
                    return type("Result", (), {"stdout": "", "stderr": ""})()

                with patch("monitor.subprocess.run", side_effect=fake_run):
                    output = create_best_moments_clip(video_path, {"title": "TVNZ highlights"})
            finally:
                os.chdir(old_cwd)

        self.assertEqual(output, str(Path("shorts") / "tvnz_moments.mp4"))

    def test_create_best_moments_clip_short_video_uses_original_directly(self):
        with TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "short.mp4"
            video_path.write_bytes(b"mp4")

            with patch("monitor.probe_video_duration_seconds", return_value=28.0), \
                 patch("monitor.subprocess.run") as run_mock:
                output = create_best_moments_clip(video_path, {"title": "Short clip"})

        self.assertEqual(output, str(video_path))
        run_mock.assert_not_called()

    def test_selected_moments_total_duration_is_limited(self):
        segments = select_best_moment_segments(150.0, [5, 45, 90, 130], max_total_seconds=50)

        total_duration = sum(end - start for start, end in segments)
        self.assertLessEqual(total_duration, 50.0)
        self.assertLessEqual(len(segments), 2)

    def test_selected_moments_include_padding_before_and_after_timestamp(self):
        segments = select_best_moment_segments(120.0, [50.0], max_total_seconds=50)

        self.assertEqual(segments, [(42.0, 64.0)])

    def test_overlapping_moment_segments_are_merged(self):
        segments = select_best_moment_segments(120.0, [50.0, 55.0], max_total_seconds=50)

        self.assertEqual(segments, [(42.0, 69.0)])

    def test_audio_peak_timestamps_are_converted_into_padded_segments(self):
        with TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "clip.mp4"
            video_path.write_bytes(b"mp4")
            astats_output = "\n".join([
                "frame:1 pts:100 pts_time:20.0",
                "lavfi.astats.Overall.RMS_level=-28.0",
                "frame:2 pts:200 pts_time:50.0",
                "lavfi.astats.Overall.RMS_level=-8.0",
                "frame:3 pts:300 pts_time:85.0",
                "lavfi.astats.Overall.RMS_level=-11.0",
            ])

            with patch("monitor.subprocess.run", return_value=type("Result", (), {"stdout": "", "stderr": astats_output})()):
                timestamps = detect_audio_peak_timestamps(video_path, 120.0, limit=2)

        self.assertEqual(timestamps, [50.0, 85.0])
        self.assertEqual(
            select_best_moment_segments(120.0, timestamps, max_total_seconds=50),
            [(42.0, 64.0), (77.0, 99.0)],
        )

    def test_create_best_moments_clip_falls_back_to_natural_windows(self):
        with TemporaryDirectory() as temp_dir:
            old_cwd = Path.cwd()
            os.chdir(temp_dir)
            try:
                video_path = Path("downloads") / "tvnz.mp4"
                video_path.parent.mkdir()
                video_path.write_bytes(b"mp4")
                captured_filters: list[str] = []

                def fake_run(command, **kwargs):
                    if command[0] == "ffprobe":
                        return type("Result", (), {"stdout": "100.0\n", "stderr": ""})()
                    if command[0] == "ffmpeg" and "astats" in " ".join(command):
                        raise subprocess.CalledProcessError(1, command, stderr="audio failed")
                    if command[0] == "ffmpeg" and "select=gt" in " ".join(command):
                        raise subprocess.CalledProcessError(1, command, stderr="scene failed")
                    captured_filters.append(" ".join(command))
                    Path(command[-1]).write_bytes(b"moments")
                    return type("Result", (), {"stdout": "", "stderr": ""})()

                with patch("monitor.subprocess.run", side_effect=fake_run):
                    output = create_best_moments_clip(video_path, {"title": "TVNZ highlights"})
            finally:
                os.chdir(old_cwd)

        self.assertEqual(output, str(Path("shorts") / "tvnz_moments.mp4"))
        self.assertTrue(any("trim=start=27.0:end=49.0" in command for command in captured_filters))
        self.assertTrue(any("trim=start=62.0:end=84.0" in command for command in captured_filters))

    def test_tvnz_highlight_video_is_accepted(self):
        self.assertTrue(is_tvnz_highlight_video({
            "channel": "TVNZ Sport",
            "title": "Portugal v Spain match highlights | FIFA World Cup",
        }))

    def test_tvnz_interview_preview_and_live_are_rejected(self):
        for title in (
            "Portugal preview before World Cup clash",
            "Coach interview after match highlights",
            "TVNZ Sport live build-up",
        ):
            with self.subTest(title=title):
                self.assertFalse(is_tvnz_highlight_video({"channel": "TVNZ Sport", "title": title}))

    def test_tvnz_backfill_limit_is_respected(self):
        completed = type("Completed", (), {
            "stdout": "\n".join([
                json.dumps({"id": "tvnz1", "title": "Portugal match highlights", "channel": "TVNZ Sport"}),
                json.dumps({"id": "tvnz2", "title": "France every goal", "channel": "TVNZ Sport"}),
            ]),
            "stderr": "",
        })()
        config = {
            "yt_dlp_bin": "yt-dlp",
            "tvnz_youtube_channel_url": "https://youtube.com/@TVNZSport/videos",
            "tvnz_backfill_limit": 2,
        }

        with patch("monitor.discover_tvnz_rss_videos", side_effect=RuntimeError("RSS unavailable")), \
             patch("monitor.subprocess.run", return_value=completed) as run_mock:
            videos = discover_tvnz_sport_videos(config)

        self.assertEqual([video["id"] for video in videos], ["tvnz1", "tvnz2"])
        command = run_mock.call_args.args[0]
        self.assertIn("--playlist-end", command)
        self.assertEqual(command[command.index("--playlist-end") + 1], "2")

    def test_tvnz_discovery_scans_30_videos_by_default(self):
        completed = type("Completed", (), {
            "stdout": json.dumps({"id": "tvnz1", "title": "Portugal match highlights", "channel": "TVNZ Sport"}),
            "stderr": "",
        })()

        with patch("monitor.get_feeds", return_value={}), \
             patch.dict(os.environ, {}, clear=True), \
             patch("monitor.discover_tvnz_rss_videos", side_effect=RuntimeError("RSS unavailable")), \
             patch("monitor.subprocess.run", return_value=completed) as run_mock:
            videos = discover_tvnz_sport_videos({"yt_dlp_bin": "yt-dlp"})

        self.assertEqual([video["id"] for video in videos], ["tvnz1"])
        command = run_mock.call_args.args[0]
        self.assertIn("https://www.youtube.com/@TVNZSport/videos", command)
        self.assertEqual(command[command.index("--playlist-end") + 1], "30")

    def test_tvnz_max_downloads_per_run_defaults_to_five(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(parse_tvnz_max_downloads_per_run({}), 5)

    def test_tvnz_rss_feed_entries_are_parsed(self):
        videos = parse_tvnz_rss_entries([{
            "yt_videoid": "rssvideo01",
            "title": "Portugal v Spain Match Highlights",
            "link": "https://www.youtube.com/watch?v=rssvideo01",
            "published": "2026-07-10T08:00:00+00:00",
        }], 30)

        self.assertEqual(videos[0]["id"], "rssvideo01")
        self.assertEqual(videos[0]["title"], "Portugal v Spain Match Highlights")
        self.assertEqual(videos[0]["webpage_url"], "https://www.youtube.com/watch?v=rssvideo01")
        self.assertEqual(videos[0]["published"], "2026-07-10T08:00:00+00:00")

    def test_tvnz_rss_discovery_returns_multiple_matching_videos(self):
        entries = [
            {"yt_videoid": "rss1", "title": "Portugal Match Highlights", "link": "https://youtu.be/rss1"},
            {"yt_videoid": "rss2", "title": "World Cup penalties shootout", "link": "https://youtu.be/rss2"},
            {"yt_videoid": "rss3", "title": "Coach interview", "link": "https://youtu.be/rss3"},
        ]
        with patch("monitor.fetch_feed_entries", return_value=entries) as fetch_mock:
            videos = discover_tvnz_sport_videos({})

        self.assertEqual([video["id"] for video in videos], ["rss1", "rss2"])
        self.assertEqual(
            fetch_mock.call_args.args[0],
            "https://www.youtube.com/feeds/videos.xml?channel_id=UCY8jpWswn6c3kpaHijtBUAg",
        )

    def test_tvnz_rss_scan_limit_is_applied_before_filtering(self):
        entries = [
            {"yt_videoid": "first", "title": "Coach interview", "link": "https://youtu.be/first"},
            {"yt_videoid": "second", "title": "Match Highlights", "link": "https://youtu.be/second"},
        ]
        with patch("monitor.fetch_feed_entries", return_value=entries):
            videos = discover_tvnz_rss_videos({"tvnz_scan_limit": 1})

        self.assertEqual([video["id"] for video in videos], ["first"])

    def test_between_two_goals_is_rejected(self):
        self.assertFalse(is_tvnz_highlight_video({
            "channel": "TVNZ Sport",
            "title": "Between Two Goals | FIFA World Cup",
        }))

    def test_monitor_workflow_sets_tvnz_limits_and_utf8(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "monitor.yml").read_text(encoding="utf-8")
        self.assertIn('TVNZ_SCAN_LIMIT: "30"', workflow)
        self.assertIn('TVNZ_MAX_DOWNLOADS_PER_RUN: "5"', workflow)
        self.assertIn('PYTHONIOENCODING: "utf-8"', workflow)
        self.assertIn('TVNZ_YOUTUBE_CHANNEL_ID: "UCY8jpWswn6c3kpaHijtBUAg"', workflow)
        self.assertIn("python -m pip install -U yt-dlp", workflow)

    def test_zero_tvnz_scan_logs_output_return_code_and_tries_fallback(self):
        empty = type("Completed", (), {
            "stdout": "primary stdout diagnostics",
            "stderr": "primary stderr diagnostics",
            "returncode": 1,
        })()

        with patch("monitor.get_feeds", return_value={}), \
             patch.dict(os.environ, {}, clear=True), \
             patch("monitor.discover_tvnz_rss_videos", side_effect=RuntimeError("RSS unavailable")), \
             patch("monitor.subprocess.run", side_effect=[empty, empty]) as run_mock, \
             self.assertLogs("football-monitor", level="INFO") as captured:
            videos = discover_tvnz_sport_videos({"yt_dlp_bin": "yt-dlp"})

        self.assertEqual(videos, [])
        self.assertEqual(run_mock.call_count, 2)
        self.assertIn("https://www.youtube.com/@TVNZSport/videos", run_mock.call_args_list[0].args[0])
        self.assertIn("https://www.youtube.com/@TVNZSport", run_mock.call_args_list[1].args[0])
        self.assertEqual(run_mock.call_args_list[0].kwargs["encoding"], "utf-8")
        self.assertEqual(run_mock.call_args_list[0].kwargs["errors"], "replace")
        logs = "\n".join(captured.output)
        self.assertIn("primary stdout diagnostics", logs)
        self.assertIn("primary stderr diagnostics", logs)
        self.assertIn("return code: 1", logs)

    def test_tvnz_discovery_accepts_multiple_new_match_highlights(self):
        completed = type("Completed", (), {
            "stdout": "\n".join([
                json.dumps({"id": "tvnz1", "title": "Portugal v Spain Match Highlights", "channel": "TVNZ Sport"}),
                json.dumps({"id": "tvnz2", "title": "France v Brazil Quarter Final", "channel": "TVNZ Sport"}),
                json.dumps({"id": "tvnz3", "title": "Argentina penalties shootout", "channel": "TVNZ Sport"}),
            ]),
            "stderr": "",
        })()

        with patch("monitor.discover_tvnz_rss_videos", side_effect=RuntimeError("RSS unavailable")), \
             patch("monitor.subprocess.run", return_value=completed):
            videos = discover_tvnz_sport_videos({"yt_dlp_bin": "yt-dlp"})

        self.assertEqual([video["id"] for video in videos], ["tvnz1", "tvnz2", "tvnz3"])

    def test_tvnz_discovery_processes_newest_videos_first(self):
        completed = type("Completed", (), {
            "stdout": "\n".join([
                json.dumps({"id": "old", "title": "Old Match Highlights", "channel": "TVNZ Sport", "upload_date": "20260708"}),
                json.dumps({"id": "new", "title": "New Match Highlights", "channel": "TVNZ Sport", "upload_date": "20260710"}),
                json.dumps({"id": "middle", "title": "Middle Match Highlights", "channel": "TVNZ Sport", "upload_date": "20260709"}),
            ]),
            "stderr": "",
        })()

        with patch("monitor.discover_tvnz_rss_videos", side_effect=RuntimeError("RSS unavailable")), \
             patch("monitor.subprocess.run", return_value=completed):
            videos = discover_tvnz_sport_videos({"yt_dlp_bin": "yt-dlp"})

        self.assertEqual([video["id"] for video in videos], ["new", "middle", "old"])

    def test_non_highlight_tvnz_videos_are_rejected(self):
        for title in (
            "Coach interview after quarter final",
            "World Cup preview show",
            "TVNZ Sport live training session",
            "Full match replay Portugal v Spain",
        ):
            with self.subTest(title=title):
                self.assertFalse(is_tvnz_highlight_video({"channel": "TVNZ Sport", "title": title}))

    def test_duplicate_tvnz_video_is_not_downloaded_twice(self):
        alerts = [{"alert_type": "tvnz_download", "video_id": "tvnz1", "video_url": "https://youtube.com/watch?v=tvnz1"}]
        with patch("monitor.discover_tvnz_sport_videos", return_value=[{
            "id": "tvnz1",
            "title": "Portugal match highlights",
            "channel": "TVNZ Sport",
            "webpage_url": "https://youtube.com/watch?v=tvnz1",
        }]), \
             patch("monitor.download_youtube_video") as download_mock:
            count = download_new_tvnz_sport_highlights({"downloads_dir": Path("downloads"), "yt_dlp_bin": "yt-dlp"}, alerts)

        self.assertEqual(count, 0)
        download_mock.assert_not_called()

    def test_duplicate_video_ids_in_same_rss_batch_are_selected_once(self):
        duplicate = {
            "id": "tvnz1",
            "title": "Portugal Match Highlights",
            "channel": "TVNZ Sport",
            "webpage_url": "https://youtube.com/watch?v=tvnz1",
        }
        with patch("monitor.discover_tvnz_sport_videos", return_value=[duplicate, dict(duplicate)]), \
             patch("monitor.download_youtube_video", return_value=None) as download_mock:
            count = download_new_tvnz_sport_highlights({"downloads_dir": Path("downloads")}, [])

        self.assertEqual(count, 0)
        download_mock.assert_called_once()

    def test_tvnz_max_downloads_per_run_is_respected(self):
        with TemporaryDirectory() as temp_dir:
            downloaded_path = str(Path(temp_dir) / "tvnz.mp4")
            moments_path = str(Path(temp_dir) / "tvnz_moments.mp4")
            vertical_path = str(Path(temp_dir) / "tvnz_vertical.mp4")
            Path(downloaded_path).write_bytes(b"mp4")
            Path(moments_path).write_bytes(b"moments")
            Path(vertical_path).write_bytes(b"short")
            alerts: list[dict] = []
            videos = [
                {"id": f"tvnz{index}", "title": f"Match Highlights {index}", "channel": "TVNZ Sport", "webpage_url": f"https://youtube.com/watch?v=tvnz{index}"}
                for index in range(1, 4)
            ]
            config = {
                "downloads_dir": Path(temp_dir),
                "yt_dlp_bin": "yt-dlp",
                "telegram_bot_token": "TEST_TOKEN",
                "telegram_chat_id": "123",
                "tvnz_max_downloads_per_run": 2,
            }

            with patch("monitor.discover_tvnz_sport_videos", return_value=videos), \
                 patch("monitor.download_youtube_video", return_value=downloaded_path) as download_mock, \
                 patch("monitor.create_best_moments_clip", return_value=moments_path), \
                 patch("monitor.create_vertical_short", return_value=vertical_path), \
                 patch("monitor.send_downloaded_video_to_telegram", return_value=True):
                count = download_new_tvnz_sport_highlights(config, alerts)

        self.assertEqual(count, 2)
        self.assertEqual(download_mock.call_count, 2)
        self.assertEqual([alert["video_id"] for alert in alerts], ["tvnz1", "tvnz2"])

    def test_failed_tvnz_telegram_delivery_is_not_marked_processed(self):
        with TemporaryDirectory() as temp_dir:
            downloaded_path = str(Path(temp_dir) / "tvnz.mp4")
            moments_path = str(Path(temp_dir) / "tvnz_moments.mp4")
            vertical_path = str(Path(temp_dir) / "tvnz_vertical.mp4")
            Path(downloaded_path).write_bytes(b"mp4")
            Path(moments_path).write_bytes(b"moments")
            Path(vertical_path).write_bytes(b"short")
            alerts: list[dict] = []
            config = {
                "downloads_dir": Path(temp_dir),
                "yt_dlp_bin": "yt-dlp",
                "telegram_bot_token": "TEST_TOKEN",
                "telegram_chat_id": "123",
            }

            with patch("monitor.discover_tvnz_sport_videos", return_value=[{
                "id": "tvnz1",
                "title": "Portugal v Spain Match Highlights",
                "channel": "TVNZ Sport",
                "webpage_url": "https://youtube.com/watch?v=tvnz1",
            }]), \
                 patch("monitor.download_youtube_video", return_value=downloaded_path), \
                 patch("monitor.create_best_moments_clip", return_value=moments_path), \
                 patch("monitor.create_vertical_short", return_value=vertical_path), \
                 patch("monitor.send_downloaded_video_to_telegram", return_value=False):
                count = download_new_tvnz_sport_highlights(config, alerts)

        self.assertEqual(count, 0)
        self.assertEqual(alerts, [])

    def test_downloaded_tvnz_video_is_converted_and_sent_as_vertical(self):
        with TemporaryDirectory() as temp_dir:
            downloaded_path = str(Path(temp_dir) / "tvnz.mp4")
            moments_path = str(Path(temp_dir) / "tvnz_moments.mp4")
            vertical_path = str(Path(temp_dir) / "tvnz_vertical.mp4")
            Path(downloaded_path).write_bytes(b"mp4")
            Path(moments_path).write_bytes(b"moments")
            Path(vertical_path).write_bytes(b"short")
            alerts: list[dict] = []
            config = {
                "downloads_dir": Path(temp_dir),
                "yt_dlp_bin": "yt-dlp",
                "telegram_bot_token": "TEST_TOKEN",
                "telegram_chat_id": "123",
            }

            with patch("monitor.discover_tvnz_sport_videos", return_value=[{
                "id": "tvnz1",
                "title": "Portugal v Spain extended highlights",
                "channel": "TVNZ Sport",
                "webpage_url": "https://youtube.com/watch?v=tvnz1",
            }]), \
                 patch("monitor.download_youtube_video", return_value=downloaded_path), \
                 patch("monitor.create_best_moments_clip", return_value=moments_path) as moments_mock, \
                 patch("monitor.create_vertical_short", return_value=vertical_path) as short_mock, \
                 patch("monitor.send_downloaded_video_to_telegram", return_value=True) as send_mock:
                count = download_new_tvnz_sport_highlights(config, alerts)

        self.assertEqual(count, 1)
        moments_mock.assert_called_once_with(downloaded_path, ANY)
        short_mock.assert_called_once()
        self.assertEqual(short_mock.call_args.args[0], moments_path)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[0], vertical_path)
        self.assertNotEqual(send_mock.call_args.args[0], downloaded_path)
        self.assertEqual(alerts[0]["video_id"], "tvnz1")
        self.assertEqual(alerts[0]["video_url"], "https://youtube.com/watch?v=tvnz1")

    def test_vertical_failure_sends_moments_clip_not_original(self):
        with TemporaryDirectory() as temp_dir:
            downloaded_path = str(Path(temp_dir) / "tvnz.mp4")
            moments_path = str(Path(temp_dir) / "tvnz_moments.mp4")
            Path(downloaded_path).write_bytes(b"mp4")
            Path(moments_path).write_bytes(b"moments")
            alerts: list[dict] = []
            config = {
                "downloads_dir": Path(temp_dir),
                "yt_dlp_bin": "yt-dlp",
                "telegram_bot_token": "TEST_TOKEN",
                "telegram_chat_id": "123",
            }

            with patch("monitor.discover_tvnz_sport_videos", return_value=[{
                "id": "tvnz1",
                "title": "Portugal v Spain extended highlights",
                "channel": "TVNZ Sport",
                "webpage_url": "https://youtube.com/watch?v=tvnz1",
            }]), \
                 patch("monitor.download_youtube_video", return_value=downloaded_path), \
                 patch("monitor.create_best_moments_clip", return_value=moments_path), \
                 patch("monitor.create_vertical_short", return_value=None), \
                 patch("monitor.send_downloaded_video_to_telegram", return_value=True) as send_mock:
                count = download_new_tvnz_sport_highlights(config, alerts)

        self.assertEqual(count, 1)
        send_mock.assert_called_once()
        self.assertEqual(send_mock.call_args.args[0], moments_path)
        self.assertNotEqual(send_mock.call_args.args[0], downloaded_path)

    def test_bbc_espn_alerts_do_not_trigger_broad_youtube_search(self):
        with TemporaryDirectory() as temp_dir:
            config = {
                "state_file": Path(temp_dir) / "state.json",
                "alerts_file": Path(temp_dir) / "alerts.json",
                "debug_mode": False,
                "telegram_bot_token": "TEST_TOKEN",
                "telegram_chat_id": "123",
                "tvnz_auto_download_enabled": False,
            }
            entries = [{
                "title": "Messi scores dramatic goal for Argentina",
                "summary": "A major football moment.",
                "description": "A major football moment.",
                "link": "https://espn.example.com/story",
                "id": "espn-1",
                "published": "2026-07-04T10:00:00Z",
            }]

            with patch("monitor.get_feeds", return_value={"ESPN FC": "https://example.com/feed"}), \
                 patch("monitor.fetch_feed_entries", return_value=entries), \
                 patch("monitor.load_seen_articles", return_value=set()), \
                 patch("monitor.save_seen_articles"), \
                 patch("monitor.load_alerts", return_value=[]), \
                 patch("monitor.save_alerts"), \
                 patch("monitor.load_todays_fixtures", return_value=[]), \
                 patch("monitor.should_send_notification", return_value=True), \
                 patch("monitor.send_telegram_notification", return_value=True), \
                 patch("monitor.search_and_download_youtube_video") as search_mock:
                process_cycle(config)

        search_mock.assert_not_called()

    def test_non_tvnz_message_shows_waiting_for_tvnz(self):
        message = build_content_discovery_telegram_message({
            "title": "Messi scores dramatic goal",
            "summary": "Big moment.",
            "sources": ["BBC Sport Football"],
            "links": ["https://bbc.example.com/story"],
            "viral_score": 82,
            "automatic_video_status": "Vídeo automático: aguardando TVNZ Sport",
        }, {})

        self.assertIn("Vídeo automático: aguardando TVNZ Sport", message)
        self.assertNotIn("youtube.com/results", message)

    def test_process_cycle_sends_vertical_file_when_created(self):
        with TemporaryDirectory() as temp_dir:
            downloaded_path = str(Path(temp_dir) / "downloaded.mp4")
            moments_path = str(Path(temp_dir) / "downloaded_moments.mp4")
            vertical_path = str(Path(temp_dir) / "vertical.mp4")
            Path(downloaded_path).write_bytes(b"mp4")
            Path(moments_path).write_bytes(b"moments")
            Path(vertical_path).write_bytes(b"short")
            config = {
                "state_file": Path(temp_dir) / "state.json",
                "alerts_file": Path(temp_dir) / "alerts.json",
                "debug_mode": False,
                "telegram_bot_token": "TEST_TOKEN",
                "telegram_chat_id": "123",
                "tvnz_auto_download_enabled": True,
                "downloads_dir": Path(temp_dir),
                "yt_dlp_bin": "yt-dlp",
            }
            entries = [{
                "title": "Messi Argentina vs Egypt Match Highlights",
                "summary": "Highlights from the match.",
                "description": "Highlights from the match.",
                "link": "https://example.com/news/1",
                "id": "news-1",
                "published": "2026-07-04T10:00:00Z",
            }]

            with patch("monitor.get_feeds", return_value={"TVNZ Sport": "https://example.com/feed"}), \
                 patch("monitor.fetch_feed_entries", return_value=entries), \
                 patch("monitor.load_seen_articles", return_value=set()), \
                 patch("monitor.save_seen_articles"), \
                 patch("monitor.load_alerts", return_value=[]), \
                 patch("monitor.save_alerts"), \
                 patch("monitor.load_todays_fixtures", return_value=[]), \
                 patch("monitor.should_send_notification", return_value=True), \
                 patch("monitor.send_telegram_notification", return_value=True), \
                 patch("monitor.discover_tvnz_sport_videos", return_value=[{
                     "id": "tvnzvideo01",
                     "title": "Messi Argentina vs Egypt match highlights",
                     "channel": "TVNZ Sport",
                     "webpage_url": "https://youtu.be/tvnzvideo01",
                 }]), \
                 patch("monitor.download_youtube_video", return_value=downloaded_path), \
                 patch("monitor.create_best_moments_clip", return_value=moments_path), \
                 patch("monitor.create_vertical_short", return_value=vertical_path), \
                 patch("monitor.send_downloaded_video_to_telegram", return_value=True) as send_video_mock:
                process_cycle(config)

        send_video_mock.assert_called_once()
        self.assertEqual(send_video_mock.call_args.args[0], vertical_path)

    def test_process_cycle_falls_back_to_original_when_vertical_creation_fails(self):
        with TemporaryDirectory() as temp_dir:
            downloaded_path = str(Path(temp_dir) / "downloaded.mp4")
            moments_path = str(Path(temp_dir) / "downloaded_moments.mp4")
            Path(downloaded_path).write_bytes(b"mp4")
            Path(moments_path).write_bytes(b"moments")
            config = {
                "state_file": Path(temp_dir) / "state.json",
                "alerts_file": Path(temp_dir) / "alerts.json",
                "debug_mode": False,
                "telegram_bot_token": "TEST_TOKEN",
                "telegram_chat_id": "123",
                "tvnz_auto_download_enabled": True,
                "downloads_dir": Path(temp_dir),
                "yt_dlp_bin": "yt-dlp",
            }
            entries = [{
                "title": "Messi Argentina vs Egypt Match Highlights",
                "summary": "Highlights from the match.",
                "description": "Highlights from the match.",
                "link": "https://example.com/news/1",
                "id": "news-1",
                "published": "2026-07-04T10:00:00Z",
            }]

            with patch("monitor.get_feeds", return_value={"TVNZ Sport": "https://example.com/feed"}), \
                 patch("monitor.fetch_feed_entries", return_value=entries), \
                 patch("monitor.load_seen_articles", return_value=set()), \
                 patch("monitor.save_seen_articles"), \
                 patch("monitor.load_alerts", return_value=[]), \
                 patch("monitor.save_alerts"), \
                 patch("monitor.load_todays_fixtures", return_value=[]), \
                 patch("monitor.should_send_notification", return_value=True), \
                 patch("monitor.send_telegram_notification", return_value=True), \
                 patch("monitor.discover_tvnz_sport_videos", return_value=[{
                     "id": "tvnzvideo01",
                     "title": "Messi Argentina vs Egypt match highlights",
                     "channel": "TVNZ Sport",
                     "webpage_url": "https://youtu.be/tvnzvideo01",
                 }]), \
                 patch("monitor.download_youtube_video", return_value=downloaded_path), \
                 patch("monitor.create_best_moments_clip", return_value=moments_path), \
                 patch("monitor.create_vertical_short", return_value=None), \
                 patch("monitor.send_downloaded_video_to_telegram", return_value=True) as send_video_mock:
                process_cycle(config)

        send_video_mock.assert_called_once()
        self.assertEqual(send_video_mock.call_args.args[0], moments_path)

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

    def test_downloads_accept_only_tvnz_sport_match_highlights(self):
        valid, _ = validate_youtube_download_candidate({
            "channel": "TVNZ Sport", "title": "Portugal v Spain extended highlights",
        })
        self.assertTrue(valid)

        for channel in ("FIFA", "BBC Sport", "ESPN FC", "CazéTV", "TVNZ"):
            with self.subTest(channel=channel):
                valid, reason = validate_youtube_download_candidate({
                    "channel": channel, "title": "Portugal v Spain match highlights",
                })
                self.assertFalse(valid)
                self.assertIn("not TVNZ Sport", reason)

    def test_tvnz_download_rejects_non_highlight_and_blocked_titles(self):
        valid, _ = validate_youtube_download_candidate({
            "channel": "TVNZ Sport", "title": "Portugal v Spain goals",
        })
        self.assertTrue(valid)

        rejected_terms = (
            "interview", "reaction", "live", "podcast", "preview",
            "press conference", "full match", "betting",
        )
        for term in rejected_terms:
            with self.subTest(term=term):
                valid, _ = validate_youtube_download_candidate({
                    "channel": "TVNZ Sport",
                    "title": f"Portugal v Spain highlights {term}",
                })
                self.assertFalse(valid)

        valid, _ = validate_youtube_download_candidate({
            "channel": "TVNZ Sport", "title": "Liverpool match highlights",
        })
        self.assertTrue(valid)

    def test_geo_blocked_cazetv_uses_requested_fallback_order(self):
        search_output = "\n".join([
            '{"id":"fifavideo01","title":"CR7 World Cup highlights","channel":"FIFA","webpage_url":"https://youtube.com/watch?v=fifavideo01"}',
            '{"id":"tvnzvideo01","title":"CR7 World Cup goals match highlights","channel":"TVNZ Sport","webpage_url":"https://youtube.com/watch?v=tvnzvideo01"}',
        ])
        completed = type("Completed", (), {"stdout": search_output, "stderr": ""})()

        with patch("monitor.subprocess.run", return_value=completed), \
             patch("monitor.download_youtube_video", return_value="downloads/tvnz.mp4") as download_mock:
            result = search_and_download_youtube_video(
                "CR7 World Cup goals",
                {},
                set(),
                preferred_url="https://youtube.com/watch?v=cazevideo01",
                trusted_source="CazéTV",
            )

        self.assertEqual(
            result,
            ("downloads/tvnz.mp4", "https://youtube.com/watch?v=tvnzvideo01"),
        )
        self.assertEqual(download_mock.call_count, 1)
        self.assertEqual(download_mock.call_args.args[0], "https://youtube.com/watch?v=tvnzvideo01")
        self.assertNotIn("cazevideo01", str(download_mock.call_args))

    def test_cazetv_source_skips_original_video_and_searches_other_sources(self):
        url = "https://www.youtube.com/watch?v=official001"
        search_result = type("Completed", (), {
            "stdout": '{"id":"tvnzvideo01","title":"GOLEADA World Cup match highlights","channel":"TVNZ Sport","webpage_url":"https://youtube.com/watch?v=tvnzvideo01"}',
            "stderr": "",
        })()
        with patch("monitor.subprocess.run", return_value=search_result) as search_mock, \
             patch("monitor.download_youtube_video", return_value="downloads/tvnz.mp4") as download_mock:
            result = search_and_download_youtube_video(
                "TODOS OS GOLS: GOLEADA NOS ANFITRIÕES E DESPEDIDA DO CR7",
                {},
                set(),
                preferred_url=url,
                trusted_source="Cazé TV",
            )

        self.assertEqual(result, ("downloads/tvnz.mp4", "https://youtube.com/watch?v=tvnzvideo01"))
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

    def test_cazetv_news_only_rejects_non_tvnz_highlights(self):
        highlight = type("Completed", (), {
            "stdout": (
                '{"id":"highlight01","title":"Portugal vs Spain highlights World Cup 2026",'
                '"uploader":"NZ Match Coverage","duration":420,"upload_date":"20260707",'
                '"webpage_url":"https://youtube.com/watch?v=highlight01"}'
            ),
            "stderr": "",
        })()
        with patch("monitor.subprocess.run", return_value=highlight), \
             patch("monitor.download_youtube_video") as download_mock, \
             self.assertLogs("football-monitor", level="INFO") as captured:
            result = search_and_download_youtube_video(
                "Portugal vs Spain highlights World Cup 2026",
                {},
                set(),
                preferred_url="https://youtube.com/watch?v=cazevideo01",
                trusted_source="CazéTV",
            )

        self.assertEqual(result, (None, None))
        download_mock.assert_not_called()
        logs = "\n".join(captured.output)
        self.assertIn("Searching non-official match highlights...", logs)
        self.assertIn("uploader is not TVNZ Sport", logs)

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
            {"id": "unrelated01", "title": "Football gaming compilation", "channel": "TVNZ Sport"},
            {"id": "official001", "title": "Messi dramatic match highlights", "channel": "TVNZ Sport"},
        ]

        selected = select_official_youtube_candidate(candidates, "Messi dramatic goal", set())

        self.assertEqual(selected["id"], "official001")

    def test_search_selection_uses_official_channel_priority(self):
        candidates = [
            {"id": "firstresult", "title": "Messi highlights", "channel": "TVNZ Sport"},
            {"id": "betterresult", "title": "Messi dramatic winning goal Argentina highlights", "channel": "TVNZ Sport"},
        ]

        selected = select_official_youtube_candidate(
            candidates, "Messi dramatic winning goal Argentina", set()
        )

        self.assertEqual(selected["id"], "betterresult")

    def test_geo_restricted_candidate_falls_back_to_next_official_upload(self):
        search_output = "\n".join([
            '{"id":"tvnzvideo01","title":"Messi match highlights","channel":"TVNZ Sport","webpage_url":"https://youtube.com/watch?v=tvnzvideo01"}',
            '{"id":"tvnzvideo02","title":"Messi extended highlights","channel":"TVNZ Sport","webpage_url":"https://youtube.com/watch?v=tvnzvideo02"}',
        ])
        completed = type("Completed", (), {"stdout": search_output, "stderr": ""})()

        with patch("monitor.subprocess.run", return_value=completed), \
             patch(
                 "monitor.download_youtube_video",
                 side_effect=[GeoRestrictedVideoError(), "downloads/tvnz.mp4"],
             ) as download_mock, \
             self.assertLogs("football-monitor", level="INFO") as captured:
            result = search_and_download_youtube_video("Messi highlights", {}, set())

        self.assertEqual(
            result,
            ("downloads/tvnz.mp4", "https://youtube.com/watch?v=tvnzvideo02"),
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
        metadata = '{"id":"official001","title":"Messi match highlights","channel":"TVNZ Sport"}'
        completed = type("Completed", (), {"stdout": metadata, "stderr": ""})()
        config = {"yt_dlp_bin": "yt-dlp", "downloads_dir": Path("downloads")}

        with patch("monitor.subprocess.run", return_value=completed) as run_mock, \
             patch("monitor.download_youtube_video", return_value="downloads/video.mp4") as download_mock:
            path, url = search_and_download_youtube_video(
                "Messi highlights", config, set(), preferred_url="https://youtu.be/official001"
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

    def test_blocked_youtube_links_add_warning_without_generic_search_link(self):
        grouped_article = {
            "title": "Messi faz golaço de falta",
            "summary": "Vídeo viral do CazéTV",
            "sources": ["CazéTV"],
            "links": ["https://www.youtube.com/watch?v=blocked123"],
            "video_url": "https://www.youtube.com/watch?v=blocked123",
            "video_status": "region_blocked",
            "search_keywords": ["Messi golaço CazéTV", "official clip"],
            "score": 7.1,
            "reason": "Tema forte",
        }

        message = build_portuguese_telegram_message(grouped_article, {})

        self.assertIn("⚠️ Este vídeo pode estar bloqueado na sua região.", message)
        self.assertNotIn("youtube.com/results", message)
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

    def test_general_news_uses_generic_telegram_template(self):
        response = type("Response", (), {"status_code": 200, "text": '{"ok":true}'})()
        article = {
            "title": "Late goals, comebacks and upsets - is record-breaking World Cup best ever?",
            "summary": "A broad tournament analysis piece.",
            "sources": ["BBC Sport"],
            "links": ["https://example.com/general-news"],
            "reason": "No AI API key configured; falling back to heuristics.",
            "content_category": "GENERAL_NEWS",
            "is_live_event": True,
        }

        with patch("monitor.requests.post", return_value=response) as post_mock:
            sent = send_telegram_notification(
                article,
                {"telegram_bot_token": "TEST_TOKEN", "telegram_chat_id": "123"},
            )

        payload = post_mock.call_args.kwargs["json"]["text"]
        self.assertTrue(sent)
        self.assertIn("Alerta de notícia", payload)
        self.assertIn("Título", payload)
        self.assertIn("Fonte", payload)
        self.assertIn("Link original", payload)
        self.assertIn("Por que importa", payload)
        self.assertIn("Ideia para Shorts", payload)
        self.assertIn("Late goals, comebacks and upsets", payload)
        self.assertIn("https://example.com/general-news", payload)
        self.assertIn("Esse assunto está movimentando o futebol", payload)
        self.assertNotIn("No AI API key configured", payload)
        self.assertNotIn("falling back to heuristics", payload)
        self.assertNotIn("Why it matters", payload)
        self.assertNotIn("Shorts idea", payload)
        self.assertNotIn("*⚽ Match:*", payload)
        self.assertNotIn("*⏱ Minute:*", payload)
        self.assertNotIn("*🥅 Goal scorer:*", payload)

    def test_transfer_news_uses_transfer_telegram_template(self):
        response = type("Response", (), {"status_code": 200, "text": '{"ok":true}'})()
        article = {
            "title": "Striker joins Barcelona on loan",
            "sources": ["Sky Sports Football"],
            "links": ["https://example.com/transfer-news"],
            "player": "Striker",
            "club": "Barcelona",
            "content_category": "TRANSFER_NEWS",
        }

        with patch("monitor.requests.post", return_value=response) as post_mock:
            sent = send_telegram_notification(
                article,
                {"telegram_bot_token": "TEST_TOKEN", "telegram_chat_id": "123"},
            )

        payload = post_mock.call_args.kwargs["json"]["text"]
        self.assertTrue(sent)
        self.assertIn("Alerta de transferência", payload)
        self.assertIn("Jogador/Clube", payload)
        self.assertIn("Fonte", payload)
        self.assertIn("Link original", payload)
        self.assertIn("Ideia para Shorts", payload)
        self.assertIn("Striker", payload)
        self.assertIn("Sky Sports Football", payload)
        self.assertIn("https://example.com/transfer-news", payload)
        self.assertNotIn("Player/club", payload)
        self.assertNotIn("Shorts idea", payload)
        self.assertNotIn("*⚽ Match:*", payload)
        self.assertNotIn("*⏱ Minute:*", payload)
        self.assertNotIn("*🥅 Goal scorer:*", payload)

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
        self.assertEqual(article["video_search_links"], [])

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

    def test_process_cycle_skips_cazetv_discussion_video_with_log(self):
        with TemporaryDirectory() as temp_dir:
            config = {
                "state_file": Path(temp_dir) / "state.json",
                "alerts_file": Path(temp_dir) / "alerts.json",
                "debug_mode": True,
                "telegram_bot_token": "",
                "telegram_chat_id": "",
            }
            entries = [{
                "title": "GERAL CAZÉTV AO VIVO: debate da Copa",
                "video_url": "https://www.youtube.com/watch?v=discussion1",
            }]

            with patch("monitor.get_feeds", return_value={"CazéTV": "https://example.com/feed"}), \
                 patch("monitor.fetch_feed_entries", return_value=entries), \
                 patch("monitor.load_seen_articles", return_value=set()), \
                 patch("monitor.load_alerts", return_value=[]), \
                 patch("monitor.save_alerts"), \
                 patch("monitor.send_telegram_notification") as telegram_mock, \
                 self.assertLogs("football-monitor", level="INFO") as captured:
                process_cycle(config)

            self.assertIn("Skipping CazéTV discussion content.", "\n".join(captured.output))
            telegram_mock.assert_not_called()

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

import unittest

from monitor import (
    build_article_key,
    build_portuguese_shorts_pack,
    build_portuguese_telegram_message,
    build_youtube_feed_url,
    group_articles,
    should_send_notification,
)


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


if __name__ == "__main__":
    unittest.main()

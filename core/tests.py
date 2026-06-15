import json
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Event, StoryPost


class ChatbotApiTests(TestCase):
    def setUp(self):
        Event.objects.create(
            title="春季導覽活動",
            slug="spring-tour",
            short_description="介紹校園水文化與導覽路線。",
            description="透過實地走訪，說明 AR 導覽與水文化故事內容。",
            date=timezone.now() + timedelta(days=5),
            location="風雲廣場",
            is_featured=True,
        )
        StoryPost.objects.create(
            title="USR 團隊紀錄",
            slug="usr-team-record",
            summary="記錄 USR 團隊的在地實作與活動成果。",
            content="這篇文章整理了 USR 團隊在校園與地方場域的合作內容。",
            category="usr",
            is_featured=True,
        )

    def _post_chat(self, message):
        return self.client.post(
            reverse("chatbot_api"),
            data=json.dumps({"message": message}),
            content_type="application/json",
        )

    @override_settings(GEMINI_API_KEY="")
    def test_chatbot_falls_back_to_local_data_when_api_key_missing(self):
        response = self._post_chat("有哪些活動可以參加？")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("春季導覽活動", payload["reply"])
        self.assertIn("USR 團隊紀錄", payload["reply"])
        self.assertTrue(payload["redirect_url"].endswith("/events/"))

    @override_settings(GEMINI_API_KEY="fake-key")
    @patch("google.genai.Client")
    def test_chatbot_falls_back_to_local_data_when_gemini_fails(self, mock_client):
        mock_client.return_value.models.generate_content.side_effect = RuntimeError("quota")

        response = self._post_chat("風雲廣場活動")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("春季導覽活動", payload["reply"])

    @override_settings(GEMINI_API_KEY="fake-key", GEMINI_MODEL="gemini-2.5-flash-lite")
    @patch("google.genai.Client")
    def test_chatbot_returns_model_reply_when_gemini_success(self, mock_client):
        class DummyResponse:
            text = "這是 Gemini 回覆"

        mock_client.return_value.models.generate_content.return_value = DummyResponse()

        response = self._post_chat("你好")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["reply"], "這是 Gemini 回覆")
        mock_client.return_value.models.generate_content.assert_called_once()
        self.assertEqual(
            mock_client.return_value.models.generate_content.call_args.kwargs["model"],
            "gemini-2.5-flash-lite",
        )

    @override_settings(GEMINI_API_KEY="")
    def test_chatbot_redirects_to_story_detail_when_query_hits_story(self):
        response = self._post_chat("帶我看 USR 團隊紀錄")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["redirect_url"].endswith("/stories/usr-team-record/"))


class ArGuidePageTests(TestCase):
    def test_ar_guide_page_renders(self):
        response = self.client.get(reverse("ar_guide"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Image Tracking AR")
        self.assertContains(response, "圖片辨識影片覆蓋導覽")
        self.assertContains(response, "ar_video1.mp4")
        self.assertContains(response, "風雲水井歷史介紹")

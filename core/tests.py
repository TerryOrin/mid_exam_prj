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
            title="水井村春季導覽",
            slug="spring-tour",
            short_description="走讀水井村人文與水文化",
            description="包含老街導覽、社區故事分享與互動體驗。",
            date=timezone.now() + timedelta(days=5),
            location="水井村活動中心",
            is_featured=True,
        )
        StoryPost.objects.create(
            title="USR 團隊進駐紀錄",
            slug="usr-team-record",
            summary="師生協力推動在地數位轉譯。",
            content="本篇整理 USR 團隊在水井村的行動成果與後續規劃。",
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
        response = self._post_chat("最近有什麼活動？")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("水井村春季導覽", payload["reply"])
        self.assertIn("USR 團隊進駐紀錄", payload["reply"])
        self.assertTrue(payload["redirect_url"].endswith("/events/"))

    @override_settings(GEMINI_API_KEY="fake-key")
    @patch("google.genai.Client")
    def test_chatbot_falls_back_to_local_data_when_gemini_fails(self, mock_client):
        mock_client.return_value.models.generate_content.side_effect = RuntimeError("quota")

        response = self._post_chat("水井村活動")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("站內可查到的資訊", payload["reply"])
        self.assertIn("水井村春季導覽", payload["reply"])

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
        response = self._post_chat("請打開 USR 團隊進駐紀錄詳情")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["redirect_url"].endswith("/stories/usr-team-record/"))

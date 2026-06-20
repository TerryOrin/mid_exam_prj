import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import ANY, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import views
from .models import Event, StoryPost
from water.models import Pond, SensorReading


class AzureSpeechTransportTests(TestCase):
    def test_http_transport_override_prefers_sdk_enum_property(self):
        class FakeSpeechConfig:
            def __init__(self):
                self.calls = []

            def set_service_property(self, *, name, value, channel):
                self.calls.append(
                    {
                        "name": name,
                        "value": value,
                        "channel": channel,
                    }
                )

        speechsdk = SimpleNamespace(
            PropertyId=SimpleNamespace(
                SpeechServiceConnection_TranslationRequestUsingConnect="enum-http-flag"
            ),
            ServicePropertyChannel=SimpleNamespace(UriQueryParameter="uri-query"),
        )
        speech_config = FakeSpeechConfig()

        views._configure_azure_http_transport(speechsdk, speech_config)

        self.assertEqual(
            speech_config.calls,
            [
                {
                    "name": "enum-http-flag",
                    "value": "false",
                    "channel": "uri-query",
                }
            ],
        )

    def test_http_transport_override_falls_back_to_raw_property_name(self):
        class FakeSpeechConfig:
            def __init__(self):
                self.calls = []

            def set_service_property(self, *, name, value, channel):
                self.calls.append(
                    {
                        "name": name,
                        "value": value,
                        "channel": channel,
                    }
                )
                if name == "enum-http-flag":
                    raise RuntimeError("unsupported enum property")

        speechsdk = SimpleNamespace(
            PropertyId=SimpleNamespace(
                SpeechServiceConnection_TranslationRequestUsingConnect="enum-http-flag"
            ),
            ServicePropertyChannel=SimpleNamespace(UriQueryParameter="uri-query"),
        )
        speech_config = FakeSpeechConfig()

        views._configure_azure_http_transport(speechsdk, speech_config)

        self.assertEqual(speech_config.calls[0]["name"], "enum-http-flag")
        self.assertEqual(
            speech_config.calls[1],
            {
                "name": "SpeechServiceConnection_TranslationRequestUsingConnect",
                "value": "false",
                "channel": "uri-query",
            },
        )


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


class AiGuideChatTests(TestCase):
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

    def _post_chat(self, user_message):
        return self.client.post(
            reverse("ai_guide_chat"),
            data=json.dumps(
                {
                    "user_message": user_message,
                    "page_path": "/",
                    "page_title": "首頁",
                }
            ),
            content_type="application/json",
        )

    @patch(
        "core.views._call_deepseek_ai_guide",
        return_value='{"reply_text":"你可以先看近期活動整理。","suggested_action":{"has_action":true,"button_label":"查看近期活動","url":"/events/"}}',
    )
    def test_ai_guide_chat_returns_structured_json(self, mock_call):
        response = self._post_chat("最近有哪些活動可以參加？")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["reply_text"], "你可以先看近期活動整理。")
        self.assertTrue(payload["suggested_action"]["has_action"])
        self.assertEqual(payload["suggested_action"]["button_label"], "查看近期活動")
        self.assertEqual(payload["suggested_action"]["url"], reverse("events_list"))
        mock_call.assert_called_once()

    @patch("core.views._call_deepseek_ai_guide", return_value="這不是 JSON，而是一段普通文字。")
    def test_ai_guide_chat_wraps_raw_reply_when_json_parse_fails(self, mock_call):
        response = self._post_chat("USR 成果有哪些重點？")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["reply_text"], "這不是 JSON，而是一段普通文字。")
        self.assertFalse(payload["suggested_action"]["has_action"])
        self.assertEqual(payload["suggested_action"]["button_label"], "")
        self.assertEqual(payload["suggested_action"]["url"], "")
        mock_call.assert_called_once()

    @patch("core.views._call_deepseek_ai_guide", side_effect=RuntimeError("timeout"))
    def test_ai_guide_chat_falls_back_to_local_action(self, mock_call):
        response = self._post_chat("我想看 IoT 智慧養殖戰情室。")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["suggested_action"]["has_action"])
        self.assertEqual(payload["suggested_action"]["url"], reverse("iot_war_room"))
        self.assertTrue(payload["suggested_action"]["button_label"])
        mock_call.assert_called_once()


class ArGuidePageTests(TestCase):
    def test_ar_guide_page_renders(self):
        response = self.client.get(reverse("ar_guide"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AR 智慧導覽")
        self.assertContains(response, "ar_video1.mp4")
        self.assertContains(response, "風雲水井歷史介紹")
        self.assertContains(response, 'id="ar-model-select"', html=False)
        self.assertContains(response, 'id="ar-iot-panel"', html=False)
        self.assertContains(response, "__AR_IOT_DATA_API_URL", html=False)


class ArGuideApiTests(TestCase):
    @patch("core.views._azure_tts_data_url", return_value="data:audio/wav;base64,ZmFrZQ==")
    @patch("core.views.shared_llm.direct_chat", return_value="這是中文導覽回覆")
    def test_ar_guide_api_uses_selected_model(self, mock_direct_chat, mock_tts):
        response = self.client.post(
            reverse("ar_ai_guide_api"),
            data=json.dumps({"text": "請介紹風雲水井", "model": "gemini-2.5-flash-lite"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["transcript"], "請介紹風雲水井")
        self.assertEqual(payload["text"], "這是中文導覽回覆")
        self.assertEqual(payload["model"]["key"], "gemini-2.5-flash-lite")
        self.assertEqual(payload["model"]["label"], "Gemini 2.5 Flash Lite")

        mock_direct_chat.assert_called_once()
        args = mock_direct_chat.call_args
        self.assertEqual(args.args[0], "請介紹風雲水井")
        self.assertEqual(args.kwargs["model_name"], "gemini-2.5-flash-lite")
        self.assertEqual(args.kwargs["system_prompt"], ANY)
        mock_tts.assert_called_once_with("這是中文導覽回覆")
        self.assertEqual(self.client.session["aiot_selected_model"], "gemini-2.5-flash-lite")

    def test_ar_guide_api_rejects_unknown_model(self):
        response = self.client.post(
            reverse("ar_ai_guide_api"),
            data=json.dumps({"text": "你好", "model": "unknown-model"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported model", response.json()["error"])


class IotWarRoomTests(TestCase):
    def _iot_headers(self):
        self.client.get(reverse("iot_war_room"))
        token = self.client.session.get("iot_browser_api_token")
        return {"HTTP_X_IOT_TOKEN": token}

    def test_iot_war_room_page_renders(self):
        response = self.client.get(reverse("iot_war_room"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "IoT 智慧養殖戰情室")
        self.assertContains(response, "iot-trend-chart")
        self.assertContains(response, 'id="diagnose-model-select"', html=False)
        self.assertContains(response, "Ammonia (NH3)")
        self.assertContains(response, "Nitrite (NO2-)")
        self.assertContains(response, reverse("iot_data_api"))
        self.assertContains(response, reverse("ai_diagnose_api"))

    def test_iot_apis_require_token(self):
        response = self.client.get(reverse("iot_data_api"))
        self.assertEqual(response.status_code, 403)
        self.assertIn("token", response.json()["error"].lower())

    def test_iot_data_api_returns_bounded_simulated_values(self):
        response = self.client.get(reverse("iot_data_api"), **self._iot_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["resource"], "iot_data")
        self.assertEqual(payload["window_hours"], 12)
        self.assertGreaterEqual(len(payload["history"]), 100)
        self.assertIn("summary", payload)
        self.assertLessEqual(payload["current"]["temperature_c"]["value"], 30.0)
        self.assertGreaterEqual(payload["current"]["temperature_c"]["value"], 20.0)
        self.assertLessEqual(payload["current"]["ph"]["value"], 8.5)
        self.assertGreaterEqual(payload["current"]["ph"]["value"], 6.5)
        self.assertLessEqual(payload["current"]["dissolved_oxygen_mg_l"]["value"], 8.0)
        self.assertGreaterEqual(payload["current"]["dissolved_oxygen_mg_l"]["value"], 4.0)

    def test_iot_data_api_aligns_with_aiot_dashboard_metrics_when_latest_readings_exist(self):
        pond = Pond.objects.create(name="示範池", species="Tilapia")
        SensorReading.objects.create(
            pond=pond,
            measured_at=timezone.now(),
            temperature=27.6,
            ph=7.45,
            dissolved_oxygen=6.3,
            ammonia=0.12,
            nitrite=0.18,
            salinity=20.0,
        )

        response = self.client.get(reverse("iot_data_api"), **self._iot_headers())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "dashboard-anchored-latest")
        self.assertEqual(payload["current"]["temperature_c"]["value"], 27.6)
        self.assertEqual(payload["current"]["ph"]["value"], 7.45)
        self.assertEqual(payload["current"]["dissolved_oxygen_mg_l"]["value"], 6.3)
        self.assertEqual(payload["summary"]["temperature_c"]["value"], 27.6)
        self.assertEqual(payload["summary"]["ammonia_mg_l"]["value"], 0.12)
        self.assertEqual(payload["summary"]["nitrite_mg_l"]["value"], 0.18)

    def test_iot_data_api_post_is_ready_for_future_ingest(self):
        response = self.client.post(
            reverse("iot_data_api"),
            data=json.dumps(
                {
                    "pond": "示範池",
                    "temperature_c": 25.1,
                    "ph": 7.4,
                    "dissolved_oxygen_mg_l": 6.2,
                }
            ),
            content_type="application/json",
            **self._iot_headers(),
        )

        self.assertEqual(response.status_code, 501)
        self.assertIn("stub", response.json()["detail"].lower())

    def test_ai_diagnose_api_returns_lightweight_advice(self):
        response = self.client.post(
            reverse("ai_diagnose_api"),
            data=json.dumps(
                {
                    "current": {
                        "temperature_c": 29.1,
                        "ph": 7.5,
                        "dissolved_oxygen_mg_l": 4.7,
                    }
                }
            ),
            content_type="application/json",
            **self._iot_headers(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["resource"], "ai_diagnose")
        self.assertEqual(payload["severity"], "alert")
        self.assertTrue(payload["facts"])
        self.assertIn("溶氧", payload["advice"])

    @patch(
        "core.views.shared_llm.direct_chat",
        return_value='{"severity":"watch","title":"建議持續觀察","advice":"建議在清晨前預備增氧。","facts":["溶氧接近警戒值。"]}',
    )
    def test_ai_diagnose_api_uses_selected_model_for_real_llm_diagnosis(self, mock_direct_chat):
        response = self.client.post(
            reverse("ai_diagnose_api"),
            data=json.dumps(
                {
                    "model": "gemini-2.5-flash-lite",
                    "current": {
                        "temperature_c": 28.4,
                        "ph": 7.6,
                        "dissolved_oxygen_mg_l": 5.1,
                    },
                }
            ),
            content_type="application/json",
            **self._iot_headers(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "selected-llm")
        self.assertEqual(payload["severity"], "watch")
        self.assertEqual(payload["model"]["key"], "gemini-2.5-flash-lite")
        self.assertEqual(payload["model"]["label"], "Gemini 2.5 Flash Lite")
        mock_direct_chat.assert_called_once()
        self.assertEqual(mock_direct_chat.call_args.kwargs["model_name"], "gemini-2.5-flash-lite")

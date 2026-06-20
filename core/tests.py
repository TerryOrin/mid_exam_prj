import io
import json
import wave
from datetime import timedelta
from unittest.mock import ANY, patch

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from fengcloud import prompts as prompt_library

from . import views
from .models import Event, StoryPost
from water.models import Pond, SensorReading


class AzureSpeechHttpTests(TestCase):
    def _wav_bytes(self, sample_rate=16000, channels=1, sample_width=2, frame_count=3200):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"\x00" * frame_count * channels * sample_width)
        return buffer.getvalue()

    @patch.dict(
        "os.environ",
        {"AZURE_SPEECH_KEY": "speech-key", "AZURE_SPEECH_REGION": "eastasia"},
        clear=False,
    )
    @patch("core.views._normalize_audio_for_azure_stt")
    @patch("core.views.requests.post")
    def test_azure_stt_uses_short_audio_rest_api(self, mock_post, mock_normalize):
        class DummyResponse:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {
                    "RecognitionStatus": "Success",
                    "DisplayText": "你好。",
                }

        mock_post.return_value = DummyResponse()
        mock_normalize.return_value = self._wav_bytes()

        transcript = views._azure_stt(self._wav_bytes(), ".wav")

        self.assertEqual(transcript, "你好")
        mock_normalize.assert_called_once()
        self.assertIn("eastasia.stt.speech.microsoft.com", mock_post.call_args.args[0])
        self.assertEqual(mock_post.call_args.kwargs["params"]["language"], "zh-TW")
        self.assertEqual(
            mock_post.call_args.kwargs["headers"]["Content-Type"],
            "audio/wav; codecs=audio/pcm; samplerate=16000",
        )

    @patch("core.views.AudioSegment.from_file")
    def test_normalize_audio_for_azure_stt_converts_mobile_webm_to_wav(self, mock_from_file):
        normalized_wav = self._wav_bytes()

        class FakeAudioSegment:
            def set_frame_rate(self, frame_rate):
                self.frame_rate = frame_rate
                return self

            def set_channels(self, channels):
                self.channels = channels
                return self

            def set_sample_width(self, sample_width):
                self.sample_width = sample_width
                return self

            def export(self, output, format="wav", codec=None):
                output.write(normalized_wav)

        mock_from_file.return_value = FakeAudioSegment()

        wav_bytes = views._normalize_audio_for_azure_stt(
            b"fake-webm-payload",
            ".webm",
            "audio/webm;codecs=opus",
        )

        self.assertEqual(wav_bytes[:4], b"RIFF")
        self.assertEqual(views._inspect_wav_audio(wav_bytes)["sample_rate"], 16000)
        self.assertEqual(mock_from_file.call_args.kwargs["format"], "webm")

    @patch.dict(
        "os.environ",
        {"AZURE_SPEECH_KEY": "speech-key", "AZURE_SPEECH_REGION": "eastasia"},
        clear=False,
    )
    @patch("core.views.requests.post")
    def test_azure_tts_data_url_uses_rest_api(self, mock_post):
        class DummyResponse:
            status_code = 200
            text = ""
            content = b"RIFFfake-wave-data"

            @staticmethod
            def json():
                raise ValueError("audio response does not include json")

        mock_post.return_value = DummyResponse()

        data_url = views._azure_tts_data_url("這是測試播報")

        self.assertTrue(data_url.startswith("data:audio/wav;base64,"))
        self.assertIn("eastasia.tts.speech.microsoft.com", mock_post.call_args.args[0])
        self.assertEqual(
            mock_post.call_args.kwargs["headers"]["X-Microsoft-OutputFormat"],
            "riff-24khz-16bit-mono-pcm",
        )
        self.assertIn(
            "zh-TW-HsiaoChenNeural",
            mock_post.call_args.kwargs["data"].decode("utf-8"),
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
        self.factory = RequestFactory()
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

    def test_ai_guide_chat_local_story_retrieval_matches_keywords(self):
        matched = views._retrieve_local_stories("可以介紹水井三寶和白馬的故事嗎？")

        self.assertTrue(matched)
        self.assertIn("水井三寶", matched[0]["matched_keywords"])
        self.assertIn("白馬", matched[0]["matched_keywords"])
        self.assertIn("姻緣花", matched[0]["content"])

    def test_ai_guide_chat_local_story_retrieval_matches_project_staff(self):
        matched = views._retrieve_local_stories("計畫主持人許永和是誰？")

        self.assertTrue(matched)
        joined_content = " ".join(item["content"] for item in matched[:3])
        self.assertIn("許永和", joined_content)
        self.assertIn("yhsheu@nfu.edu.tw", joined_content)

    @patch.dict("os.environ", {"DEEPSEEK_API_KEY": "deepseek-test-key"}, clear=False)
    @patch("core.views.requests.post")
    def test_call_deepseek_ai_guide_uses_requests_and_includes_story_context(self, mock_post):
        class DummyResponse:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"reply_text":"水井三寶象徵在地記憶。",'
                                    '"suggested_action":{"has_action":false,"button_label":"","url":""}}'
                                )
                            }
                        }
                    ]
                }

        mock_post.return_value = DummyResponse()
        request = self.factory.get(reverse("home"))

        raw_reply = views._call_deepseek_ai_guide(
            request,
            "請介紹水井三寶的故事",
            page_path="/",
            page_title="首頁",
        )

        self.assertIn("reply_text", raw_reply)
        self.assertIn("/chat/completions", mock_post.call_args.args[0])
        sent_payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_payload["model"], views.AI_GUIDE_CHAT_MODEL)
        self.assertIn("獨家參考資料", sent_payload["messages"][0]["content"])
        self.assertIn("水井三寶", sent_payload["messages"][0]["content"])

    def test_chat_prompt_route_table_includes_aiot_and_iot_pages(self):
        route_rules = prompt_library.build_route_rules_table(include_api=False)

        self.assertIn("/aiot-water-assistant/", route_rules)
        self.assertIn("/iot-war-room/", route_rules)


class ArGuidePageTests(TestCase):
    def test_ar_guide_page_renders(self):
        response = self.client.get(reverse("ar_guide"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AR 智慧導覽")
        self.assertContains(response, "ar_video1.mp4")
        self.assertContains(response, "風雲水井歷史介紹")
        self.assertContains(response, 'id="ar-model-select"', html=False)
        self.assertContains(response, 'id="ar-iot-panel"', html=False)
        self.assertContains(response, 'data-marker-key="history"', html=False)
        self.assertContains(response, "__AR_IOT_DATA_API_URL", html=False)


class ArGuideApiTests(TestCase):
    @patch("core.views._azure_tts_data_url", return_value="data:audio/wav;base64,ZmFrZQ==")
    @patch("core.views.shared_llm.direct_chat", return_value="這是中文導覽回覆")
    def test_ar_guide_api_uses_selected_model(self, mock_direct_chat, mock_tts):
        response = self.client.post(
            reverse("ar_ai_guide_api"),
            data=json.dumps(
                {
                    "text": "請介紹風雲水井",
                    "model": "gemini-2.5-flash-lite",
                    "current_marker": "waterwheel",
                }
            ),
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
        self.assertIn("機電與物理專家", args.kwargs["system_prompt"])
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

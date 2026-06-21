import json
from unittest.mock import patch

from django.core.cache import caches
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from water.models import Pond, SensorReading

from . import llm, tools, views


class LlmConfigTests(SimpleTestCase):
    def test_available_models_include_deepseek_flash(self):
        model = llm.resolve_model("deepseek-v4-flash")
        self.assertEqual(model.provider, "deepseek")
        self.assertTrue(
            any(option["key"] == "deepseek-v4-flash" for option in llm.get_available_models())
        )
        self.assertFalse(any(option["key"] == "gpt-4o-mini" for option in llm.get_available_models()))
        self.assertFalse(any(option["key"] == "llama3" for option in llm.get_available_models()))

    @patch.dict(
        "os.environ",
        {
            "LLM_PROVIDER": "deepseek",
            "LLM_MODEL": "",
            "DS_API_KEY": "deepseek-test-key",
            "GEMINI_API_KEY": "",
        },
        clear=False,
    )
    def test_default_model_uses_deepseek_when_configured(self):
        self.assertEqual(llm.get_default_model_name(), "deepseek-v4-flash")

    @patch.dict(
        "os.environ",
        {
            "DS_API_KEY": "deepseek-test-key",
            "DEEPSEEK_API_KEY": "",
        },
        clear=False,
    )
    @patch("openai.OpenAI")
    def test_deepseek_client_uses_openai_compatible_base_url(self, mock_openai):
        llm._client(llm.resolve_model("deepseek-v4-flash"))

        mock_openai.assert_called_once_with(
            api_key="deepseek-test-key",
            base_url=llm.DEEPSEEK_OPENAI_BASE_URL,
        )

    def test_deepseek_request_enables_reasoning(self):
        kwargs = llm._completion_request_kwargs(
            llm.resolve_model("deepseek-v4-flash"),
            [{"role": "user", "content": "hello"}],
        )

        self.assertEqual(kwargs["reasoning_effort"], "high")
        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "enabled"}})


class WaterQualityAssistantTests(TestCase):
    def setUp(self):
        for alias in ("default", "ai_rate_limit"):
            try:
                caches[alias].clear()
            except Exception:  # noqa: BLE001
                continue
        self.pond = Pond.objects.create(name="Test Pond", species="Tilapia")
        SensorReading.objects.create(
            pond=self.pond,
            measured_at=timezone.now(),
            temperature=29.4,
            ph=8.1,
            dissolved_oxygen=5.8,
            ammonia=0.31,
            nitrite=0.62,
            salinity=22.0,
        )

    def test_latest_water_quality_includes_ammonia_and_nitrite(self):
        payload = tools.get_latest_water_quality("Test Pond")

        self.assertEqual(payload["ammonia_mg_l"], 0.31)
        self.assertEqual(payload["nitrite_mg_l"], 0.62)

    def test_thresholds_include_ammonia_and_nitrite_alerts(self):
        payload = tools.check_thresholds("Test Pond")

        self.assertIn("Ammonia (NH3) is above 0.2 mg/L.", payload["alerts"])
        self.assertIn("Nitrite (NO2-) is above 0.5 mg/L.", payload["alerts"])

    def test_dashboard_metrics_include_ammonia_and_nitrite(self):
        payload = views._dashboard_payload()

        self.assertEqual(payload["metrics"]["ammonia_mg_l"], 0.31)
        self.assertEqual(payload["metrics"]["nitrite_mg_l"], 0.62)
        self.assertEqual(payload["ponds"][0]["ammonia_mg_l"], 0.31)
        self.assertEqual(payload["ponds"][0]["nitrite_mg_l"], 0.62)

    def test_dashboard_api_returns_sidebar_metrics_only(self):
        response = self.client.get(reverse("chat:dashboard-api"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("war_room", payload)
        self.assertEqual(payload["metrics"]["temperature_c"], 29.4)
        self.assertEqual(payload["metrics"]["ph"], 8.1)
        self.assertEqual(payload["metrics"]["dissolved_oxygen_mg_l"], 5.8)

    def test_chat_page_keeps_assistant_layout(self):
        response = self.client.get(reverse("chat:page"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AIOT 水質助手")
        self.assertNotContains(response, "AIOT Smart Operations")

    def test_aiot_assistant_prompt_includes_current_metrics(self):
        prompt = views._aiot_assistant_system_prompt()

        self.assertIn("29.40 °C", prompt)
        self.assertIn("8.10", prompt)
        self.assertIn("5.80 mg/L", prompt)

    @patch("chat.views.llm.chat_with_system_prompt")
    def test_chat_api_rejects_large_code_payload_before_llm_call(self, mock_chat):
        code_payload = (
            "```python\n"
            "from pathlib import Path\n"
            "def run_script():\n"
            "    print('hello world')\n"
            "    return Path('data.json').read_text()\n"
            "pip install django\n"
            "SELECT * FROM pond_readings;\n"
            "```"
        ) * 3

        response = self.client.post(
            reverse("chat:chat-api"),
            data=json.dumps({"message": code_payload}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("技術 payload", response.json()["error"])
        mock_chat.assert_not_called()

    @patch("chat.views.llm.chat_with_system_prompt", return_value="收到，目前先持續觀察。")
    @override_settings(
        AI_GUARD_RATE_LIMITS={
            "chat_minute": {"limit": 60, "window_seconds": 60},
            "chat_hour": {"limit": 120, "window_seconds": 3600},
            "chat_day": {"limit": 240, "window_seconds": 86400},
            "ar_voice_minute": {"limit": 3, "window_seconds": 60},
            "ar_voice_hour": {"limit": 12, "window_seconds": 3600},
            "ar_voice_day": {"limit": 25, "window_seconds": 86400},
            "ai_global_day": {"limit": 400, "window_seconds": 86400},
        }
    )
    def test_chat_api_trims_history_to_recent_10_rounds_and_6000_chars(self, mock_chat):
        for index in range(20):
            message = (
                f"第 {index + 1} 輪請分析魚塭水質與增氧建議，"
                "我想知道溫度、pH、溶氧、巡池安排與投餌節奏。"
            )
            response = self.client.post(
                reverse("chat:chat-api"),
                data=json.dumps({"message": message}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)

        history = mock_chat.call_args.kwargs["history"]
        total_chars = sum(len(item["content"]) for item in history)

        self.assertLessEqual(len(history), 20)
        self.assertLessEqual(total_chars, 6000)

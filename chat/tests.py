from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
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

from services.ai_insights_service import AIInsightsService, DEFAULT_OPENAI_MODEL


def test_sanitize_context_removes_sensitive_fields_and_binary_values():
    raw = {
        "transactions": [{"amount": 50, "note": "coffee"}],
        "access_token": "secret-access",
        "nested": {
            "refresh_token": "secret-refresh",
            "safe_value": "ok",
            "attachment_bytes": b"file-bytes",
        },
        "proof_bytes": b"proof",
    }

    sanitized = AIInsightsService.sanitize_context(raw)

    assert "access_token" not in sanitized
    assert "proof_bytes" not in sanitized
    assert "refresh_token" not in sanitized["nested"]
    assert "attachment_bytes" not in sanitized["nested"]
    assert sanitized["nested"]["safe_value"] == "ok"
    assert sanitized["transactions"][0]["amount"] == 50


def test_context_snapshot_is_stable_after_key_order_changes():
    first = {"cash_flow": {"net": 10, "income": 20}, "month": "2026-يونيو"}
    second = {"month": "2026-يونيو", "cash_flow": {"income": 20, "net": 10}}

    assert AIInsightsService.context_snapshot(first) == AIInsightsService.context_snapshot(second)


def test_from_runtime_reads_nested_openai_config():
    service = AIInsightsService.from_runtime(
        {
            "openai": {
                "api_key": "test-key",
                "model": "custom-model",
                "api_url": "https://example.com/chat",
            }
        }
    )

    assert service.api_key == "test-key"
    assert service.model == "custom-model"
    assert service.api_url == "https://example.com/chat"


def test_generate_cash_flow_brief_returns_unconfigured_without_network_call():
    called = False

    def fake_post(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network should not be called")

    service = AIInsightsService(post_func=fake_post)
    result = service.generate_cash_flow_brief({"cash_flow_90d": {"projected_next_90": {"net": 100}}})

    assert called is False
    assert result["ok"] is False
    assert result["error"] == "AI is not configured."


def test_generate_cash_flow_brief_posts_sanitized_context_and_parses_json():
    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"summary":"الوضع جيد","risks":["هبوط الكاش"],'
                                '"opportunities":["خفض المصاريف"],'
                                '"next_actions":["راجع الفواتير"],'
                                '"data_gaps":["أضف معاملات أكثر"],"confidence":"medium"}'
                            )
                        }
                    }
                ]
            }

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    service = AIInsightsService(
        "openai-key",
        model="test-model",
        api_url="https://example.com/chat",
        timeout_sec=7,
        post_func=fake_post,
    )
    result = service.generate_cash_flow_brief(
        {
            "cash_flow_90d": {"projected_next_90": {"net": 100}},
            "refresh_token": "must-not-leak",
        },
        language="ar",
    )

    assert result["ok"] is True
    assert result["summary"] == "الوضع جيد"
    assert result["confidence"] == "medium"
    assert captured["url"] == "https://example.com/chat"
    assert captured["headers"]["Authorization"] == "Bearer openai-key"
    assert captured["json"]["model"] == "test-model"
    assert captured["timeout"] == 7

    request_text = captured["json"]["messages"][1]["content"]
    assert "must-not-leak" not in request_text
    assert "refresh_token" not in request_text


def _calm_brief(decision_ids=None):
    ids = decision_ids or [
        "follow_up_open_items",
        "reduce_category_spike",
        "protect_cash_90d",
    ]
    return {
        "schema_version": "financial-calm-brief-v1",
        "facts": [{"fact_id": "approved_amount", "kind": "money", "value": 850.0}],
        "decisions": [
            {
                "decision_id": decision_id,
                "rank": rank,
                "title_en": "Approved decision",
                "fact_ids": ["approved_amount"],
            }
            for rank, decision_id in enumerate(ids, start=1)
        ],
    }


def test_openai_model_has_one_central_default_and_one_environment_override(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    default_service = AIInsightsService.from_runtime({"OPENAI_API_KEY": "test-key"})
    assert default_service.model == DEFAULT_OPENAI_MODEL

    monkeypatch.setenv("OPENAI_MODEL", "alternate-model")
    overridden_service = AIInsightsService.from_runtime({"OPENAI_API_KEY": "test-key"})
    assert overridden_service.model == "alternate-model"


def test_numeric_validation_applies_only_to_user_facing_explanation_text():
    expected_ids = [
        "follow_up_open_items",
        "reduce_category_spike",
        "protect_cash_90d",
    ]
    allowed = AIInsightsService.validate_calm_explanations(
        {
            "schema_version": 1,
            "explanations": [
                {"decision_id": "follow_up_open_items", "explanation": "ابدئي بالعناصر المفتوحة لتخفيف الضغط."},
                {"decision_id": "reduce_category_spike", "explanation": "هذه الفئة أوضح فرصة لتهدئة الصرف."},
                {
                    "decision_id": "protect_cash_90d",
                    "rank": 3,
                    "explanation": "حماية الهامش تساعدك قبل أي التزام جديد.",
                },
            ],
        },
        expected_ids,
    )
    rejected = AIInsightsService.validate_calm_explanations(
        {
            "explanations": [
                {"decision_id": "follow_up_open_items", "explanation": "ابدئي بتحصيل 850 د.ك."},
                {"decision_id": "reduce_category_spike", "explanation": "هذه الفئة تحتاج تهدئة."},
                {"decision_id": "protect_cash_90d", "explanation": "حماية الهامش تساعدك."},
            ],
        },
        expected_ids,
    )

    assert allowed["ok"] is True
    assert "protect_cash_90d" in allowed["explanations"]
    assert rejected["ok"] is False
    assert rejected["error"] == "AI explanation included a number."


def test_spelled_out_quantity_in_explanation_is_rejected():
    result = AIInsightsService.validate_calm_explanations(
        {
            "explanations": [
                {"decision_id": "follow_up_open_items", "explanation": "Review one open item first."},
                {"decision_id": "reduce_category_spike", "explanation": "Ease the clearest spending pressure."},
                {"decision_id": "protect_cash_90d", "explanation": "Protect the available room."},
            ],
        },
        ["follow_up_open_items", "reduce_category_spike", "protect_cash_90d"],
    )

    assert result["ok"] is False
    assert result["error"] == "AI explanation included a number."


def test_calm_explanations_fail_closed_when_ai_is_unconfigured():
    called = False

    def fake_post(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network should not be called")

    result = AIInsightsService(post_func=fake_post).generate_financial_calm_explanations(
        _calm_brief()
    )

    assert called is False
    assert result["ok"] is False
    assert result["error"] == "AI is not configured."


def test_calm_explanations_use_selected_model_and_accept_number_free_copy():
    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"explanations":['
                                '{"decision_id":"follow_up_open_items","explanation":"ابدئي بالمبالغ المفتوحة لتخفيف الضغط."},'
                                '{"decision_id":"reduce_category_spike","explanation":"هذه الفئة أوضح فرصة لتهدئة الصرف."},'
                                '{"decision_id":"protect_cash_90d","explanation":"حماية الهامش تدعم القرارات القادمة."}'
                                "]}"
                            )
                        }
                    }
                ]
            }

    def fake_post(url, headers, json, timeout):
        captured["json"] = json
        return FakeResponse()

    service = AIInsightsService(
        "openai-key",
        model="selected-model",
        post_func=fake_post,
    )
    result = service.generate_financial_calm_explanations(_calm_brief(), language="ar")

    assert result["ok"] is True
    assert len(result["explanations"]) == 3
    assert captured["json"]["model"] == "selected-model"
    request_text = captured["json"]["messages"][1]["content"]
    assert "approved_amount" in request_text
    assert "850.0" in request_text

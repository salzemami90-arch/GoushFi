from services.ai_insights_service import AIInsightsService


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

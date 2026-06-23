from services.supabase_sync import SupabaseSyncClient


class FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_cloudflare_html_error_is_sanitized():
    raw_html = "<!DOCTYPE html><html><title>521: Web server is down</title>Cloudflare</html>"

    message = SupabaseSyncClient._friendly_error(
        FakeResponse(521),
        raw_html,
        ("msg", "error_description", "error"),
    )

    assert "Supabase is temporarily unavailable" in message
    assert "<html" not in message.lower()
    assert "Cloudflare" not in message


def test_json_error_message_is_preserved():
    message = SupabaseSyncClient._friendly_error(
        FakeResponse(400),
        {"error_description": "Invalid login credentials"},
        ("msg", "error_description", "error"),
    )

    assert message == "Invalid login credentials"


def test_password_reset_uses_recover_endpoint(monkeypatch):
    captured = {}

    class RecoverResponse:
        status_code = 200

        @staticmethod
        def json():
            return {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return RecoverResponse()

    monkeypatch.setattr("services.supabase_sync.requests.post", fake_post)

    client = SupabaseSyncClient("https://example.supabase.co", "anon-key", timeout_sec=7)
    result = client.request_password_reset(" user@example.com ")

    assert result["ok"] is True
    assert captured["url"] == "https://example.supabase.co/auth/v1/recover"
    assert captured["json"] == {"email": "user@example.com"}
    assert captured["headers"]["apikey"] == "anon-key"
    assert captured["timeout"] == 7


def test_refresh_session_uses_refresh_token_endpoint(monkeypatch):
    captured = {}

    class RefreshResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "user": {"id": "user-1", "email": "user@example.com"},
            }

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return RefreshResponse()

    monkeypatch.setattr("services.supabase_sync.requests.post", fake_post)

    client = SupabaseSyncClient("https://example.supabase.co", "anon-key", timeout_sec=7)
    result = client.refresh_session("old-refresh")

    assert result["ok"] is True
    assert result["access_token"] == "new-access"
    assert result["refresh_token"] == "new-refresh"
    assert result["user"]["id"] == "user-1"
    assert captured["url"] == "https://example.supabase.co/auth/v1/token?grant_type=refresh_token"
    assert captured["json"] == {"refresh_token": "old-refresh"}
    assert captured["headers"]["apikey"] == "anon-key"
    assert captured["timeout"] == 7


def test_sign_out_uses_logout_endpoint_with_bearer_token(monkeypatch):
    captured = {}

    class LogoutResponse:
        status_code = 204

        @staticmethod
        def json():
            return {}

    def fake_post(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return LogoutResponse()

    monkeypatch.setattr("services.supabase_sync.requests.post", fake_post)

    client = SupabaseSyncClient("https://example.supabase.co", "anon-key", timeout_sec=7)
    result = client.sign_out("access-token")

    assert result["ok"] is True
    assert captured["url"] == "https://example.supabase.co/auth/v1/logout"
    assert captured["headers"]["apikey"] == "anon-key"
    assert captured["headers"]["Authorization"] == "Bearer access-token"
    assert captured["timeout"] == 7


def test_delete_current_user_requires_service_role():
    client = SupabaseSyncClient("https://example.supabase.co", "anon-key", timeout_sec=7)

    result = client.delete_current_user("user-1")

    assert result["ok"] is False
    assert result["error"] == "account_delete_requires_service_role"


def test_delete_current_user_uses_admin_endpoint_with_service_role(monkeypatch):
    captured = {}

    class DeleteUserResponse:
        status_code = 200

        @staticmethod
        def json():
            return {}

    def fake_delete(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return DeleteUserResponse()

    monkeypatch.setattr("services.supabase_sync.requests.delete", fake_delete)

    client = SupabaseSyncClient(
        "https://example.supabase.co",
        "anon-key",
        timeout_sec=7,
        service_role_key="service-role-key",
    )
    result = client.delete_current_user("user-1")

    assert result["ok"] is True
    assert captured["url"] == "https://example.supabase.co/auth/v1/admin/users/user-1"
    assert captured["headers"]["apikey"] == "service-role-key"
    assert captured["headers"]["Authorization"] == "Bearer service-role-key"
    assert captured["timeout"] == 7

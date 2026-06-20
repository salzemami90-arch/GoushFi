from services.supabase_sync import SupabaseSyncClient


def test_build_apple_oauth_authorize_url_encodes_redirect_and_scopes():
    client = SupabaseSyncClient("https://example.supabase.co", "anon-key")

    url = client.build_oauth_authorize_url(
        "apple",
        "https://goushfi.up.railway.app/?page=settings&f_lang=ar",
        scopes="name email",
        code_challenge="challenge-123",
        code_challenge_method="S256",
        state="state-123",
    )

    assert url.startswith("https://example.supabase.co/auth/v1/authorize?")
    assert "provider=apple" in url
    assert "redirect_to=https%3A%2F%2Fgoushfi.up.railway.app%2F%3Fpage%3Dsettings%26f_lang%3Dar" in url
    assert "scopes=name+email" in url
    assert "code_challenge=challenge-123" in url
    assert "code_challenge_method=S256" in url
    assert "flow_type=pkce" in url
    assert "state=state-123" in url


def test_build_oauth_authorize_url_rejects_unknown_provider():
    client = SupabaseSyncClient("https://example.supabase.co", "anon-key")

    assert client.build_oauth_authorize_url("unknown", "https://goushfi.up.railway.app/") == ""


def test_build_oauth_authorize_url_requires_config():
    client = SupabaseSyncClient("", "")

    assert client.build_oauth_authorize_url("apple", "https://goushfi.up.railway.app/") == ""


def test_exchange_pkce_code_uses_pkce_token_endpoint(monkeypatch):
    captured = {}

    class TokenResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "user": {"id": "user-123", "email": "user@example.com"},
            }

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return TokenResponse()

    monkeypatch.setattr("services.supabase_sync.requests.post", fake_post)

    client = SupabaseSyncClient("https://example.supabase.co", "anon-key", timeout_sec=9)
    result = client.exchange_pkce_code("auth-code", "code-verifier")

    assert result["ok"] is True
    assert result["access_token"] == "access-token"
    assert result["refresh_token"] == "refresh-token"
    assert result["user"]["id"] == "user-123"
    assert captured["url"] == "https://example.supabase.co/auth/v1/token?grant_type=pkce"
    assert captured["json"] == {"auth_code": "auth-code", "code_verifier": "code-verifier"}
    assert captured["headers"]["apikey"] == "anon-key"
    assert captured["timeout"] == 9

import base64
import hashlib
from types import SimpleNamespace

from services import oauth_pkce


def test_create_pkce_flow_can_pop_matching_verifier():
    oauth_pkce._PKCE_STORE.clear()

    flow = oauth_pkce.create_pkce_flow()
    verifier = oauth_pkce.pop_pkce_verifier(flow["state"])
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")

    assert verifier == flow["code_verifier"]
    assert flow["code_challenge"] == expected_challenge
    assert flow["code_challenge_method"] == "S256"
    assert oauth_pkce.pop_pkce_verifier(flow["state"]) == ""


def test_pop_pkce_verifier_rejects_missing_state():
    oauth_pkce._PKCE_STORE.clear()

    assert oauth_pkce.pop_pkce_verifier("missing") == ""
    assert oauth_pkce.pop_pkce_verifier("") == ""


def test_get_or_create_pkce_flow_reuses_pending_session_flow():
    oauth_pkce._PKCE_STORE.clear()
    session_state = {}

    first = oauth_pkce.get_or_create_pkce_flow(session_state)
    second = oauth_pkce.get_or_create_pkce_flow(session_state)

    assert second["state"] == first["state"]
    assert second["code_verifier"] == first["code_verifier"]
    assert session_state[oauth_pkce.PKCE_SESSION_STATE_KEY]["state"] == first["state"]


def test_resolve_pkce_verifier_can_use_cookie_when_callback_has_no_state():
    oauth_pkce._PKCE_STORE.clear()
    flow = oauth_pkce.create_pkce_flow()
    encoded = oauth_pkce._encode_cookie_flow(flow)
    cookie_flow = oauth_pkce._decode_cookie_flow(encoded)

    assert oauth_pkce.resolve_pkce_verifier("", cookie_flow=cookie_flow, session_state={}) == flow["code_verifier"]


def test_read_pkce_cookie_accepts_cookie_header():
    oauth_pkce._PKCE_STORE.clear()
    flow = oauth_pkce.create_pkce_flow()
    encoded = oauth_pkce._encode_cookie_flow(flow)
    context = SimpleNamespace(cookies={}, headers={"cookie": f"other=1; {oauth_pkce.PKCE_COOKIE_NAME}={encoded}"})

    assert oauth_pkce.read_pkce_cookie(context)["code_verifier"] == flow["code_verifier"]


def test_render_pkce_cookie_writes_short_lived_cookie(monkeypatch):
    oauth_pkce._PKCE_STORE.clear()
    flow = oauth_pkce.create_pkce_flow()
    captured = {}

    def fake_html(html, height=0, width=0):
        captured["html"] = html
        captured["height"] = height
        captured["width"] = width

    monkeypatch.setattr("services.oauth_pkce.components.html", fake_html)

    oauth_pkce.render_pkce_cookie(flow)

    html = captured["html"]
    assert oauth_pkce.PKCE_COOKIE_NAME in html
    assert "SameSite=Lax" in html
    assert "maxAge = 900" in html
    assert captured["height"] == 0
    assert captured["width"] == 0


def test_resolve_pkce_verifier_uses_durable_store_after_session_is_lost(monkeypatch, tmp_path):
    oauth_pkce._PKCE_STORE.clear()
    monkeypatch.setattr(oauth_pkce, "PKCE_SQLITE_FILE", str(tmp_path / "pkce.sqlite3"))
    session_state = {}

    flow = oauth_pkce.get_or_create_pkce_flow(session_state)
    oauth_pkce._PKCE_STORE.clear()

    assert oauth_pkce.resolve_pkce_verifier(flow["state"], session_state={}) == flow["code_verifier"]


def test_forget_pkce_flow_deletes_durable_store(monkeypatch, tmp_path):
    oauth_pkce._PKCE_STORE.clear()
    monkeypatch.setattr(oauth_pkce, "PKCE_SQLITE_FILE", str(tmp_path / "pkce.sqlite3"))
    session_state = {}

    flow = oauth_pkce.get_or_create_pkce_flow(session_state)
    oauth_pkce.forget_pkce_flow(session_state)
    oauth_pkce._PKCE_STORE.clear()

    assert oauth_pkce.resolve_pkce_verifier(flow["state"], session_state={}) == ""

from services import cloud_state_helpers
from services.cloud_sync_guard import (
    PAUSE_REASON_KEY,
    READY_USER_KEY,
    cloud_sync_ready_for_user,
)
from pages_floosy import settings_page


class _FakeSt:
    def __init__(self):
        self.session_state = {
            "cloud_auth": {
                "logged_in": True,
                "email": "old@example.com",
                "user_id": "user-old",
                "access_token": "old-access",
                "refresh_token": "old-refresh",
            },
            "_cloud_remember_login": True,
        }
        self.context = None


class _RefreshClient:
    def __init__(self, result):
        self.result = result
        self.seen_refresh_token = ""

    def refresh_session(self, refresh_token):
        self.seen_refresh_token = refresh_token
        return self.result


def test_manual_cloud_action_refreshes_expired_access_token(monkeypatch):
    fake_st = _FakeSt()
    remembered = {}
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(cloud_state_helpers, "st", fake_st)
    monkeypatch.setattr(
        settings_page,
        "remember_cloud_auth",
        lambda email, user_id, refresh_token: remembered.update(
            {"email": email, "user_id": user_id, "refresh_token": refresh_token}
        ),
    )
    client = _RefreshClient(
        {
            "ok": True,
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "user": {"id": "user-new", "email": "new@example.com"},
        }
    )

    cloud_auth, error = settings_page._refresh_cloud_auth_for_manual_action(client)

    assert error == ""
    assert client.seen_refresh_token == "old-refresh"
    assert cloud_auth["access_token"] == "new-access"
    assert cloud_auth["refresh_token"] == "new-refresh"
    assert cloud_auth["user_id"] == "user-new"
    assert fake_st.session_state["_cloud_auth_issued_at"]
    assert remembered == {
        "email": "new@example.com",
        "user_id": "user-new",
        "refresh_token": "new-refresh",
    }


def test_manual_cloud_action_reports_refresh_failure(monkeypatch):
    fake_st = _FakeSt()
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(cloud_state_helpers, "st", fake_st)
    client = _RefreshClient({"ok": False, "error": "JWT expired"})

    cloud_auth, error = settings_page._refresh_cloud_auth_for_manual_action(client)

    assert cloud_auth["access_token"] == "old-access"
    assert error == "JWT expired"
    assert fake_st.session_state["_cloud_sync_last_error"] == "token_refresh_failed"


class _InitialCloudCopyClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def upsert_user_data(self, user_id, access_token, payload):
        self.calls.append(
            {
                "user_id": user_id,
                "access_token": access_token,
                "payload": payload,
            }
        )
        return self.result


def test_initial_cloud_copy_after_sign_up_marks_sync_ready(monkeypatch):
    fake_st = _FakeSt()
    fake_st.session_state["settings"] = {"cloud_sync_enabled": True, "cloud_last_sync_at": ""}
    fake_st.session_state["app_scope"] = {"owner_user_id": "", "owner_email": ""}
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(cloud_state_helpers, "st", fake_st)
    monkeypatch.setattr(
        settings_page,
        "export_app_state_payload",
        lambda: {
            "settings": dict(fake_st.session_state["settings"]),
            "app_scope": dict(fake_st.session_state["app_scope"]),
        },
    )
    saved = {"called": False}
    monkeypatch.setattr(settings_page, "save_persistent_state", lambda: saved.update({"called": True}))
    client = _InitialCloudCopyClient({"ok": True})

    ok, error = settings_page._create_initial_cloud_copy_after_sign_up(
        client,
        "user-new",
        "access-new",
        "new@example.com",
    )

    assert ok is True
    assert error == ""
    assert saved["called"] is True
    assert client.calls[0]["user_id"] == "user-new"
    assert client.calls[0]["access_token"] == "access-new"
    assert fake_st.session_state["app_scope"] == {
        "owner_user_id": "user-new",
        "owner_email": "new@example.com",
    }
    assert cloud_sync_ready_for_user(fake_st.session_state, "user-new") is True
    assert fake_st.session_state[PAUSE_REASON_KEY] == ""
    assert fake_st.session_state["_cloud_sync_last_error"] == ""
    assert fake_st.session_state["settings"]["cloud_last_sync_at"]
    assert fake_st.session_state["_cloud_last_snapshot"]


def test_initial_cloud_copy_after_sign_up_pauses_on_push_failure(monkeypatch):
    fake_st = _FakeSt()
    fake_st.session_state["settings"] = {"cloud_sync_enabled": True, "cloud_last_sync_at": ""}
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(cloud_state_helpers, "st", fake_st)
    monkeypatch.setattr(settings_page, "export_app_state_payload", lambda: {"settings": {}})
    saved = {"called": False}
    monkeypatch.setattr(settings_page, "save_persistent_state", lambda: saved.update({"called": True}))
    client = _InitialCloudCopyClient({"ok": False, "error": "insert failed"})

    ok, error = settings_page._create_initial_cloud_copy_after_sign_up(
        client,
        "user-new",
        "access-new",
        "new@example.com",
    )

    assert ok is False
    assert error == "insert failed"
    assert saved["called"] is True
    assert fake_st.session_state["_cloud_sync_last_error"] == "insert failed"
    assert fake_st.session_state[READY_USER_KEY] == ""
    assert fake_st.session_state[PAUSE_REASON_KEY] == "initial_cloud_copy_failed_after_sign_up"


def test_remember_login_reload_is_not_for_localhost_when_persistence_is_off(monkeypatch):
    fake_st = _FakeSt()
    fake_st.context = type("Context", (), {"url": "http://127.0.0.1:8507"})()
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(settings_page, "_local_persistence_enabled", lambda: False)

    assert settings_page._cloud_remember_reload_after_write() is False


def test_remember_login_reload_stays_on_for_shared_hosted(monkeypatch):
    fake_st = _FakeSt()
    fake_st.context = type("Context", (), {"url": "https://goush-beta.streamlit.app"})()
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(settings_page, "_local_persistence_enabled", lambda: False)

    assert settings_page._cloud_remember_reload_after_write() is True


def test_cloud_oauth_redirect_url_carries_pkce_state_and_strips_stale_callback_params(monkeypatch):
    fake_st = _FakeSt()
    fake_st.context = type(
        "Context",
        (),
        {"url": "https://goushfi.up.railway.app/?page=settings&code=old&state=old&f_lang=en"},
    )()
    fake_st.query_params = {}
    monkeypatch.setattr(settings_page, "st", fake_st)

    redirect_url = settings_page._cloud_oauth_redirect_url("ar", "pkce-state-123")

    assert redirect_url == (
        "https://goushfi.up.railway.app/?page=settings&f_lang=ar&cloud_oauth=apple"
        "&cloud_pkce_state=pkce-state-123&f_w=1"
    )


def test_same_tab_oauth_button_renders_self_target(monkeypatch):
    fake_st = _FakeSt()
    captured = {}

    def fake_markdown(markup, unsafe_allow_html=False):
        captured["markup"] = markup
        captured["unsafe_allow_html"] = unsafe_allow_html

    fake_st.markdown = fake_markdown
    monkeypatch.setattr(settings_page, "st", fake_st)

    settings_page._render_same_tab_oauth_button("Continue with Apple", "https://example.com/auth?x=1&y=2")

    assert 'target="_self"' in captured["markup"]
    assert "https://example.com/auth?x=1&amp;y=2" in captured["markup"]
    assert "Continue with Apple" in captured["markup"]
    assert captured["unsafe_allow_html"] is True


def test_same_tab_oauth_redirect_attempts_parent_navigation(monkeypatch):
    fake_st = _FakeSt()
    captured = {}

    def fake_html(markup, height=0):
        captured["component_markup"] = markup
        captured["height"] = height

    def fake_caption(text):
        captured["caption"] = text

    def fake_markdown(markup, unsafe_allow_html=False):
        captured["link_markup"] = markup
        captured["unsafe_allow_html"] = unsafe_allow_html

    fake_st.caption = fake_caption
    fake_st.markdown = fake_markdown
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(settings_page.components, "html", fake_html)

    settings_page._render_same_tab_oauth_redirect(
        "Continue with Apple",
        "https://example.com/auth?x=1&y=2",
    )

    assert "window.parent" in captured["component_markup"]
    assert "location.assign" in captured["component_markup"]
    assert "https://example.com/auth?x=1&y=2" in captured["component_markup"]
    assert captured["height"] == 0
    assert captured["caption"] == "Opening Apple sign-in..."
    assert 'target="_self"' in captured["link_markup"]
    assert "https://example.com/auth?x=1&amp;y=2" in captured["link_markup"]
    assert captured["unsafe_allow_html"] is True

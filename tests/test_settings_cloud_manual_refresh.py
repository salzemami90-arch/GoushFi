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


class _SignOutClient:
    def __init__(self):
        self.seen_access_token = ""

    def sign_out(self, access_token):
        self.seen_access_token = access_token
        return {"ok": True}


class _FailingSignOutClient:
    def sign_out(self, access_token):
        raise RuntimeError("network down")


class _FreshStartClient:
    is_configured = True

    def __init__(self, delete_result):
        self.delete_result = delete_result
        self.seen_refresh_token = ""
        self.deleted = []
        self.signed_out = []

    def refresh_session(self, refresh_token):
        self.seen_refresh_token = refresh_token
        return {
            "ok": True,
            "access_token": "fresh-access",
            "refresh_token": "fresh-refresh",
            "user": {"id": "user-old", "email": "old@example.com"},
        }

    def delete_user_data(self, user_id, access_token):
        self.deleted.append({"user_id": user_id, "access_token": access_token})
        return self.delete_result

    def sign_out(self, access_token):
        self.signed_out.append(access_token)
        return {"ok": True}


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


def test_cloud_sign_out_clears_local_auth_and_browser_storage(monkeypatch):
    fake_st = _FakeSt()
    fake_st.session_state["app_scope"] = {"owner_user_id": "user-old", "owner_email": "old@example.com"}
    fake_st.session_state[READY_USER_KEY] = "user-old"
    fake_st.session_state[PAUSE_REASON_KEY] = "some_reason"
    cleared = {}
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(cloud_state_helpers, "st", fake_st)
    monkeypatch.setattr(
        settings_page,
        "clear_cloud_auth_cookie",
        lambda reload_after_write=False: cleared.update({"reload_after_write": reload_after_write}),
    )
    client = _SignOutClient()

    settings_page._sign_out_cloud_session(client)

    assert client.seen_access_token == "old-access"
    assert fake_st.session_state["cloud_auth"]["logged_in"] is False
    assert fake_st.session_state["_cloud_remember_login"] is False
    assert fake_st.session_state["_cloud_cookie_restore_checked"] is True
    assert fake_st.session_state["_cloud_browser_storage_clear_requested"] is True
    assert fake_st.session_state["_cloud_auth_cookie_clear_pending"] is True
    assert fake_st.session_state["app_scope"] == {"owner_user_id": "", "owner_email": ""}
    assert fake_st.session_state[READY_USER_KEY] == ""
    assert fake_st.session_state[PAUSE_REASON_KEY] == ""
    assert cleared == {"reload_after_write": False}


def test_cloud_sign_out_still_clears_local_state_when_network_fails(monkeypatch):
    fake_st = _FakeSt()
    cleared = {}
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(cloud_state_helpers, "st", fake_st)
    monkeypatch.setattr(
        settings_page,
        "clear_cloud_auth_cookie",
        lambda reload_after_write=False: cleared.update({"reload_after_write": reload_after_write}),
    )

    settings_page._sign_out_cloud_session(_FailingSignOutClient())

    assert fake_st.session_state["cloud_auth"]["logged_in"] is False
    assert fake_st.session_state["_cloud_browser_storage_clear_requested"] is True
    assert fake_st.session_state["_cloud_auth_cookie_clear_pending"] is True
    assert cleared == {"reload_after_write": False}


def test_pending_cloud_auth_clear_renders_once(monkeypatch):
    fake_st = _FakeSt()
    fake_st.session_state["_cloud_auth_cookie_clear_pending"] = True
    cleared = []
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(
        settings_page,
        "clear_cloud_auth_cookie",
        lambda reload_after_write=False: cleared.append(reload_after_write),
    )

    settings_page._render_pending_cloud_auth_clear()
    settings_page._render_pending_cloud_auth_clear()

    assert "_cloud_auth_cookie_clear_pending" not in fake_st.session_state
    assert cleared == [False]


def test_fresh_start_deletes_cloud_then_clears_local_and_signs_out(monkeypatch):
    fake_st = _FakeSt()
    fake_st.session_state["settings"] = {"cloud_sync_enabled": True}
    fake_st.session_state["transactions"] = {"2026-06": [{"amount": 229}]}
    fake_st.session_state["app_scope"] = {"owner_user_id": "user-old", "owner_email": "old@example.com"}
    cleared = {}
    reset = {"called": False}
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(cloud_state_helpers, "st", fake_st)
    monkeypatch.setattr(settings_page, "remember_cloud_auth", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        settings_page,
        "clear_cloud_auth_cookie",
        lambda reload_after_write=False: cleared.update({"reload_after_write": reload_after_write}),
    )

    def fake_reset_local_app_data():
        reset["called"] = True
        fake_st.session_state["settings"] = {"cloud_sync_enabled": False}
        fake_st.session_state["transactions"] = {}
        fake_st.session_state["app_scope"] = {"owner_user_id": "", "owner_email": ""}

    monkeypatch.setattr(settings_page, "reset_local_app_data", fake_reset_local_app_data)
    client = _FreshStartClient({"ok": True})

    ok, error = settings_page._fresh_start_app_data(client)

    assert ok is True
    assert error == ""
    assert client.seen_refresh_token == "old-refresh"
    assert client.deleted == [{"user_id": "user-old", "access_token": "fresh-access"}]
    assert client.signed_out == ["fresh-access"]
    assert reset["called"] is True
    assert fake_st.session_state["transactions"] == {}
    assert fake_st.session_state["cloud_auth"]["logged_in"] is False
    assert fake_st.session_state["_device_browser_storage_clear_requested"] is True
    assert fake_st.session_state["_cloud_browser_storage_clear_requested"] is True
    assert fake_st.session_state["_cloud_remember_login"] is False
    assert cleared == {"reload_after_write": False}


def test_fresh_start_keeps_local_data_when_cloud_delete_fails(monkeypatch):
    fake_st = _FakeSt()
    fake_st.session_state["settings"] = {"cloud_sync_enabled": True}
    fake_st.session_state["transactions"] = {"2026-06": [{"amount": 229}]}
    reset = {"called": False}
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(cloud_state_helpers, "st", fake_st)
    monkeypatch.setattr(settings_page, "remember_cloud_auth", lambda *args, **kwargs: None)
    monkeypatch.setattr(settings_page, "reset_local_app_data", lambda: reset.update({"called": True}))
    client = _FreshStartClient({"ok": False, "error": "delete failed"})

    ok, error = settings_page._fresh_start_app_data(client)

    assert ok is False
    assert error == "delete failed"
    assert reset["called"] is False
    assert fake_st.session_state["transactions"] == {"2026-06": [{"amount": 229}]}
    assert fake_st.session_state["cloud_auth"]["logged_in"] is True


def test_fresh_start_reports_local_reset_failure_without_crashing(monkeypatch):
    fake_st = _FakeSt()
    fake_st.session_state["cloud_auth"] = {"logged_in": False}
    fake_st.session_state["settings"] = {"cloud_sync_enabled": False}
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(cloud_state_helpers, "st", fake_st)
    monkeypatch.setattr(settings_page, "clear_cloud_auth_cookie", lambda reload_after_write=False: None)
    monkeypatch.setattr(settings_page, "reset_local_app_data", lambda: (_ for _ in ()).throw(RuntimeError("storage failed")))

    ok, error = settings_page._fresh_start_app_data(None)

    assert ok is False
    assert "storage failed" in error
    assert fake_st.session_state["_cloud_browser_storage_clear_requested"] is True


def test_save_method_normalizes_both_enabled_to_cloud_only(monkeypatch):
    fake_st = _FakeSt()
    fake_st.session_state["settings"] = {"device_save_enabled": True, "cloud_sync_enabled": True}
    monkeypatch.setattr(settings_page, "st", fake_st)

    device_enabled, cloud_enabled = settings_page._save_method_widget_state(device_save_available=True)

    assert device_enabled is False
    assert cloud_enabled is True
    assert fake_st.session_state["settings"]["device_save_enabled"] is False
    assert fake_st.session_state["settings"]["cloud_sync_enabled"] is True


def test_save_method_callbacks_make_options_mutually_exclusive(monkeypatch):
    fake_st = _FakeSt()
    fake_st.session_state["settings_device_save_enabled"] = True
    fake_st.session_state["settings_cloud_sync_enabled"] = True
    monkeypatch.setattr(settings_page, "st", fake_st)

    settings_page._on_device_save_method_changed()

    assert fake_st.session_state["settings_device_save_enabled"] is True
    assert fake_st.session_state["settings_cloud_sync_enabled"] is False

    fake_st.session_state["settings_device_save_enabled"] = True
    fake_st.session_state["settings_cloud_sync_enabled"] = True

    settings_page._on_cloud_save_method_changed()

    assert fake_st.session_state["settings_device_save_enabled"] is False
    assert fake_st.session_state["settings_cloud_sync_enabled"] is True


def test_empty_local_payload_clears_stale_cloud_conflict_notice(monkeypatch):
    fake_st = _FakeSt()
    fake_st.session_state[PAUSE_REASON_KEY] = "local_cloud_conflict_after_sign_in"
    warnings = []
    fake_st.warning = lambda message: warnings.append(message)
    monkeypatch.setattr(settings_page, "st", fake_st)
    monkeypatch.setattr(
        settings_page,
        "export_app_state_payload",
        lambda: {
            "transactions": {},
            "savings": {"2026-06": {"goal": 0, "transactions": []}},
            "project_data": {"2026-06": {"projects": {}}},
        },
    )

    settings_page._render_cloud_sync_pause_notice(lambda ar, en: en)

    assert warnings == []
    assert fake_st.session_state[PAUSE_REASON_KEY] == ""
    assert cloud_sync_ready_for_user(fake_st.session_state, "user-old") is True


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


def test_native_apple_button_uses_ios_bridge_scheme(monkeypatch):
    fake_st = _FakeSt()
    captured = {}

    def fake_markdown(markup, unsafe_allow_html=False):
        captured["markup"] = markup
        captured["unsafe_allow_html"] = unsafe_allow_html

    fake_st.markdown = fake_markdown
    monkeypatch.setattr(settings_page, "st", fake_st)

    settings_page._render_native_apple_sign_in_button("Continue with Apple")

    assert 'target="_self"' in captured["markup"]
    assert "goushfi://native-apple-sign-in" in captured["markup"]
    assert "Continue with Apple" in captured["markup"]
    assert captured["unsafe_allow_html"] is True


def test_native_shell_apple_oauth_uses_implicit_flow(monkeypatch):
    fake_st = _FakeSt()
    fake_st.query_params = {"f_shell": "1"}
    monkeypatch.setattr(settings_page, "st", fake_st)

    assert settings_page._native_shell_oauth_uses_implicit_flow() is True

    fake_st.query_params = {}

    assert settings_page._native_shell_oauth_uses_implicit_flow() is False


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

from pathlib import Path
from types import SimpleNamespace

from services.cloud_auth_cookie import (
    bootstrap_cloud_auth_from_storage,
    clear_cloud_auth_cookie,
    read_cloud_auth_cookie,
    remember_cloud_auth,
    render_cloud_oauth_callback_capture,
    render_cloud_oauth_hash_capture_inline,
    sync_cloud_auth_browser_storage,
)


def test_remember_cloud_auth_renders_hosted_cookie_variants(monkeypatch):
    captured = {}

    def fake_html(html, height=0, width=0):
        captured["html"] = html
        captured["height"] = height
        captured["width"] = width

    monkeypatch.setattr("services.cloud_auth_cookie.components.html", fake_html)

    remember_cloud_auth("user@example.com", "user-123", "refresh-token-xyz")

    html = captured["html"]
    assert "SameSite=Lax" in html
    assert "SameSite=None; Secure" in html
    assert "Partitioned" in html
    assert "localStorage" in html
    assert "floosy_cloud_auth_storage" in html
    assert "window.top" in html
    assert "collectWindows" in html
    assert "current.parent && current.parent !== current" in html
    assert "domain=${hostname}" in html
    assert captured["height"] == 0
    assert captured["width"] == 0


def test_remember_cloud_auth_can_request_reload_after_write(monkeypatch):
    captured = {}

    def fake_html(html, height=0, width=0):
        captured["html"] = html

    monkeypatch.setattr("services.cloud_auth_cookie.components.html", fake_html)

    remember_cloud_auth("user@example.com", "user-123", "refresh-token-xyz", reload_after_write=True)

    html = captured["html"]
    assert 'const shouldReloadAfterWrite = true;' in html
    assert "window.location.replace" in html
    assert "window.location.reload" in html


def test_clear_cloud_auth_cookie_uses_zero_max_age(monkeypatch):
    captured = {}

    def fake_html(html, height=0, width=0):
        captured["html"] = html

    monkeypatch.setattr("services.cloud_auth_cookie.components.html", fake_html)

    clear_cloud_auth_cookie()

    html = captured["html"]
    assert "const maxAge = 0;" in html
    assert "max-age=${maxAge}" in html
    assert "Thu, 01 Jan 1970 00:00:00 GMT" in html


def test_render_cloud_oauth_callback_capture_uses_browser_bridge_payload(monkeypatch):
    service = __import__("services.cloud_auth_cookie", fromlist=["dummy"])
    encoded = service._encode_payload(
        {
            "email": "user@example.com",
            "user_id": "user-123",
            "refresh_token": "refresh-token-xyz",
        }
    )
    captured = {}
    backups = []
    cookie_scripts = []
    fake_st = SimpleNamespace(session_state={"_cloud_cookie_restore_checked": True})

    def fake_component(**kwargs):
        captured.update(kwargs)
        return encoded

    monkeypatch.setattr("services.cloud_auth_cookie.st", fake_st)
    monkeypatch.setattr("services.cloud_auth_cookie._is_native_shell_runtime", lambda: False)
    monkeypatch.setattr("services.cloud_auth_cookie._BROWSER_STORAGE_BRIDGE", fake_component)
    monkeypatch.setattr("services.cloud_auth_cookie._write_local_auth_backup", lambda payload: backups.append(payload))
    monkeypatch.setattr(
        "services.cloud_auth_cookie._render_cookie_script",
        lambda value, max_age, reload_after_write=False: cookie_scripts.append((value, max_age, reload_after_write)),
    )

    result = render_cloud_oauth_callback_capture()

    assert result == {
        "email": "user@example.com",
        "user_id": "user-123",
        "refresh_token": "refresh-token-xyz",
    }
    assert captured["action"] == "capture_oauth"
    assert captured["storageName"] == "floosy_cloud_auth_storage"
    assert backups == [result]
    assert cookie_scripts == [(encoded, service.COOKIE_MAX_AGE_SECONDS, True)]
    assert fake_st.session_state["_cloud_cookie_restore_checked"] is False


def test_cloud_auth_browser_bridge_captures_oauth_hash():
    html = Path("components/cloud_auth_browser_bridge/index.html").read_text()

    assert "refresh_token" in html
    assert "access_token" in html
    assert "decodeJwtPayload" in html
    assert "localStorage" in html
    assert 'action === "capture_oauth"' in html
    assert "captureOAuthValue" in html
    assert "sourceLocation.replace(cleanUrl)" in html


def test_render_cloud_oauth_hash_capture_inline_reads_parent_location_hash(monkeypatch):
    captured = {}

    def fake_markdown(html, unsafe_allow_html=False):
        captured["html"] = html
        captured["unsafe_allow_html"] = unsafe_allow_html

    monkeypatch.setattr("services.cloud_auth_cookie.st.markdown", fake_markdown)
    monkeypatch.setattr("services.cloud_auth_cookie._is_native_shell_runtime", lambda: False)

    render_cloud_oauth_hash_capture_inline()

    html = captured["html"]
    assert "window.location.hash" in html
    assert "refresh_token" in html
    assert "access_token" in html
    assert "window.localStorage.setItem" in html
    assert "document.cookie" in html
    assert "window.history.replaceState" in html
    assert "window.location.reload" in html
    assert captured["unsafe_allow_html"] is True


def test_read_cloud_auth_cookie_decodes_payload(monkeypatch):
    service = __import__("services.cloud_auth_cookie", fromlist=["dummy"])
    encoded = service._encode_payload(
        {
            "email": "user@example.com",
            "user_id": "user-123",
            "refresh_token": "refresh-token-xyz",
        }
    )

    fake_st = SimpleNamespace(context=SimpleNamespace(cookies={service.COOKIE_NAME: encoded}))
    monkeypatch.setattr("services.cloud_auth_cookie.st", fake_st)

    assert read_cloud_auth_cookie() == {
        "email": "user@example.com",
        "user_id": "user-123",
        "refresh_token": "refresh-token-xyz",
    }


def test_read_cloud_auth_cookie_returns_empty_when_missing_refresh_token(monkeypatch):
    service = __import__("services.cloud_auth_cookie", fromlist=["dummy"])
    encoded = service._encode_payload(
        {
            "email": "user@example.com",
            "user_id": "user-123",
        }
    )

    fake_st = SimpleNamespace(context=SimpleNamespace(cookies={service.COOKIE_NAME: encoded}))
    monkeypatch.setattr("services.cloud_auth_cookie.st", fake_st)

    assert read_cloud_auth_cookie() == {}


def test_read_cloud_auth_cookie_falls_back_to_cookie_header(monkeypatch):
    service = __import__("services.cloud_auth_cookie", fromlist=["dummy"])
    encoded = service._encode_payload(
        {
            "email": "user@example.com",
            "user_id": "user-123",
            "refresh_token": "refresh-token-xyz",
        }
    )

    fake_st = SimpleNamespace(
        context=SimpleNamespace(
            cookies={},
            headers={"cookie": f"another=value; {service.COOKIE_NAME}={encoded}; theme=dark"},
        )
    )
    monkeypatch.setattr("services.cloud_auth_cookie.st", fake_st)

    assert read_cloud_auth_cookie() == {
        "email": "user@example.com",
        "user_id": "user-123",
        "refresh_token": "refresh-token-xyz",
    }


def test_read_cloud_auth_cookie_falls_back_to_local_backup_on_localhost(monkeypatch):
    fake_st = SimpleNamespace(context=SimpleNamespace(cookies={}, headers={}, url="http://localhost:8501"))
    monkeypatch.setattr("services.cloud_auth_cookie.st", fake_st)
    monkeypatch.setattr(
        "services.cloud_auth_cookie.load_sqlite_payload",
        lambda path: {
            "email": "user@example.com",
            "user_id": "user-123",
            "refresh_token": "refresh-token-xyz",
        },
    )

    assert read_cloud_auth_cookie() == {
        "email": "user@example.com",
        "user_id": "user-123",
        "refresh_token": "refresh-token-xyz",
    }


def test_read_cloud_auth_cookie_uses_local_backup_when_runtime_url_is_blank(monkeypatch):
    fake_st = SimpleNamespace(context=SimpleNamespace(cookies={}, headers={}, url=""))
    monkeypatch.setattr("services.cloud_auth_cookie.st", fake_st)
    monkeypatch.setattr("services.cloud_auth_cookie.Path.exists", lambda self: str(self).endswith("floosy_cloud_auth.sqlite3"))
    monkeypatch.setattr(
        "services.cloud_auth_cookie.load_sqlite_payload",
        lambda path: {
            "email": "user@example.com",
            "user_id": "user-123",
            "refresh_token": "refresh-token-xyz",
        },
    )

    assert read_cloud_auth_cookie() == {
        "email": "user@example.com",
        "user_id": "user-123",
        "refresh_token": "refresh-token-xyz",
    }


def test_remember_cloud_auth_uses_config_local_persistence_signal(monkeypatch):
    fake_st = SimpleNamespace(context=SimpleNamespace(cookies={}, headers={}, url=""))
    monkeypatch.setattr("services.cloud_auth_cookie.st", fake_st)

    captured = {}

    def fake_save(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return True

    monkeypatch.setattr("services.cloud_auth_cookie.save_sqlite_payload", fake_save)
    monkeypatch.setattr("services.cloud_auth_cookie.components.html", lambda html, height=0, width=0: None)
    monkeypatch.setattr("config_floosy._local_persistence_enabled", lambda: True)

    remember_cloud_auth("user@example.com", "user-123", "refresh-token-xyz")

    assert captured["payload"]["refresh_token"] == "refresh-token-xyz"


def test_bootstrap_cloud_auth_from_storage_renders_reload_bridge(monkeypatch):
    captured = {}

    def fake_html(html, height=0, width=0):
        captured["html"] = html
        captured["height"] = height
        captured["width"] = width

    monkeypatch.setattr("services.cloud_auth_cookie.components.html", fake_html)

    bootstrap_cloud_auth_from_storage()

    html = captured["html"]
    assert "floosy_cloud_auth_storage" in html
    assert "sessionStorage" in html
    assert "location.replace" in html
    assert "bootFlag" in html
    assert "collectWindows" in html
    assert "window.top" in html
    assert captured["height"] == 0
    assert captured["width"] == 0


def test_sync_cloud_auth_browser_storage_returns_pending_until_frontend_replies(monkeypatch):
    monkeypatch.setattr(
        "services.cloud_auth_cookie._BROWSER_STORAGE_BRIDGE",
        lambda **kwargs: "__PENDING__",
    )

    payload, ready = sync_cloud_auth_browser_storage()

    assert payload == {}
    assert ready is False


def test_sync_cloud_auth_browser_storage_skips_component_in_native_shell(monkeypatch):
    fake_st = SimpleNamespace(query_params={"f_shell": "1"})
    monkeypatch.setattr("config_floosy.st", fake_st)

    def fail_component(**kwargs):
        raise AssertionError("native shell should not render the browser storage component")

    monkeypatch.setattr("services.cloud_auth_cookie._BROWSER_STORAGE_BRIDGE", fail_component)

    payload, ready = sync_cloud_auth_browser_storage(
        {
            "email": "user@example.com",
            "user_id": "user-123",
            "refresh_token": "refresh-token-xyz",
        }
    )

    assert payload == {}
    assert ready is True


def test_sync_cloud_auth_browser_storage_decodes_returned_payload(monkeypatch):
    service = __import__("services.cloud_auth_cookie", fromlist=["dummy"])
    encoded = service._encode_payload(
        {
            "email": "user@example.com",
            "user_id": "user-123",
            "refresh_token": "refresh-token-xyz",
        }
    )

    captured = {}

    def fake_component(**kwargs):
        captured.update(kwargs)
        return encoded

    monkeypatch.setattr("services.cloud_auth_cookie._BROWSER_STORAGE_BRIDGE", fake_component)

    payload, ready = sync_cloud_auth_browser_storage(
        {
            "email": "user@example.com",
            "user_id": "user-123",
            "refresh_token": "refresh-token-xyz",
        }
    )

    assert ready is True
    assert payload == {
        "email": "user@example.com",
        "user_id": "user-123",
        "refresh_token": "refresh-token-xyz",
    }
    assert captured["action"] == "sync"
    assert captured["storageName"] == "floosy_cloud_auth_storage"


def test_sync_cloud_auth_browser_storage_can_clear_saved_value(monkeypatch):
    captured = {}

    def fake_component(**kwargs):
        captured.update(kwargs)
        return ""

    monkeypatch.setattr("services.cloud_auth_cookie._BROWSER_STORAGE_BRIDGE", fake_component)

    payload, ready = sync_cloud_auth_browser_storage(clear=True)

    assert ready is True
    assert payload == {}
    assert captured["action"] == "clear"
    assert captured["value"] == ""


def test_remember_cloud_auth_writes_local_backup_on_localhost(monkeypatch):
    fake_st = SimpleNamespace(context=SimpleNamespace(cookies={}, headers={}, url="http://localhost:8501"))
    monkeypatch.setattr("services.cloud_auth_cookie.st", fake_st)

    captured = {}

    def fake_save(path, payload):
        captured["path"] = path
        captured["payload"] = payload
        return True

    monkeypatch.setattr("services.cloud_auth_cookie.save_sqlite_payload", fake_save)
    monkeypatch.setattr("services.cloud_auth_cookie.components.html", lambda html, height=0, width=0: None)

    remember_cloud_auth("user@example.com", "user-123", "refresh-token-xyz")

    assert captured["payload"] == {
        "email": "user@example.com",
        "user_id": "user-123",
        "refresh_token": "refresh-token-xyz",
    }


def test_clear_cloud_auth_cookie_clears_local_backup_on_localhost(monkeypatch):
    fake_st = SimpleNamespace(context=SimpleNamespace(cookies={}, headers={}, url="http://localhost:8501"))
    monkeypatch.setattr("services.cloud_auth_cookie.st", fake_st)

    captured = {}

    monkeypatch.setattr("services.cloud_auth_cookie.delete_sqlite_payload", lambda path: captured.setdefault("path", path))
    monkeypatch.setattr("services.cloud_auth_cookie.components.html", lambda html, height=0, width=0: None)

    clear_cloud_auth_cookie()

    assert captured["path"].endswith("floosy_cloud_auth.sqlite3")

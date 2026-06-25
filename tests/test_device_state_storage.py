from pathlib import Path
from types import SimpleNamespace

from services import device_state_storage
from services.device_state_storage import (
    sync_device_state_browser_storage,
)


def test_device_state_storage_sync_encodes_and_decodes_payload(monkeypatch):
    payload = {"settings": {"device_save_enabled": True}, "transactions": {"2026-06": [{"amount": 1}]}}
    encoded = device_state_storage._encode_payload(payload)
    captured = {}

    def fake_component(**kwargs):
        captured.update(kwargs)
        return encoded

    monkeypatch.setattr(device_state_storage, "_BROWSER_STORAGE_BRIDGE", fake_component)

    restored, ready = sync_device_state_browser_storage(payload, key="device_state_test")

    assert ready is True
    assert restored == payload
    assert captured["action"] == "sync"
    assert captured["storageName"] == device_state_storage.STORAGE_NAME
    assert captured["value"] == encoded


def test_device_state_storage_read_does_not_write(monkeypatch):
    captured = {}

    def fake_component(**kwargs):
        captured.update(kwargs)
        return ""

    monkeypatch.setattr(device_state_storage, "_BROWSER_STORAGE_BRIDGE", fake_component)

    restored, ready = sync_device_state_browser_storage(enabled=False, key="device_state_read_test")

    assert ready is True
    assert restored == {}
    assert captured["action"] == "read"
    assert captured["value"] == ""


def test_device_state_storage_clear_removes_saved_value(monkeypatch):
    captured = {}

    def fake_component(**kwargs):
        captured.update(kwargs)
        return ""

    monkeypatch.setattr(device_state_storage, "_BROWSER_STORAGE_BRIDGE", fake_component)

    restored, ready = sync_device_state_browser_storage(clear=True, key="device_state_clear_test")

    assert ready is True
    assert restored == {}
    assert captured["action"] == "clear"
    assert captured["value"] == ""


def test_device_state_storage_skips_component_in_native_shell(monkeypatch):
    captured = {}
    fake_st = SimpleNamespace(query_params={})
    monkeypatch.setattr(device_state_storage, "_is_native_shell_runtime", lambda: True)
    monkeypatch.setattr(device_state_storage, "st", fake_st)

    def fail_component(**kwargs):
        raise AssertionError("native shell should not render the device storage component")

    monkeypatch.setattr(device_state_storage, "_BROWSER_STORAGE_BRIDGE", fail_component)
    monkeypatch.setattr(device_state_storage.components, "html", lambda html, height=0, width=0: captured.update({"html": html, "height": height, "width": width}))

    restored, ready = sync_device_state_browser_storage(
        {"settings": {"device_save_enabled": True}},
        key="device_state_native_shell_test",
    )

    assert ready is True
    assert restored == {}
    assert "localStorage" in captured["html"]
    assert "sync" in captured["html"]
    assert captured["height"] == 0
    assert device_state_storage.browser_device_storage_available() is True


def test_device_state_storage_reads_native_shell_query_payload(monkeypatch):
    payload = {"settings": {"device_save_enabled": True}, "transactions": {"2026-06": [{"amount": 7}]}}
    encoded = device_state_storage._encode_payload(payload)
    fake_st = SimpleNamespace(
        query_params={
            device_state_storage.QUERY_READY_PARAM: "1",
            device_state_storage.QUERY_VALUE_PARAM: encoded,
        }
    )
    monkeypatch.setattr(device_state_storage, "_is_native_shell_runtime", lambda: True)
    monkeypatch.setattr(device_state_storage, "st", fake_st)

    restored, ready = sync_device_state_browser_storage(enabled=False, key="device_state_native_read_test")

    assert ready is True
    assert restored == payload
    assert device_state_storage.QUERY_READY_PARAM not in fake_st.query_params
    assert device_state_storage.QUERY_VALUE_PARAM not in fake_st.query_params


def test_device_state_storage_native_read_renders_redirect_bridge(monkeypatch):
    captured = {}
    fake_st = SimpleNamespace(query_params={})
    monkeypatch.setattr(device_state_storage, "_is_native_shell_runtime", lambda: True)
    monkeypatch.setattr(device_state_storage, "st", fake_st)
    monkeypatch.setattr(device_state_storage.components, "html", lambda html, height=0, width=0: captured.update({"html": html}))

    restored, ready = sync_device_state_browser_storage(enabled=False, key="device_state_native_pending_test")

    assert ready is False
    assert restored == {}
    assert "navigateWithStoredValue" in captured["html"]
    assert device_state_storage.QUERY_READY_PARAM in captured["html"]


def test_device_state_bridge_clear_uses_safe_indexeddb_cleanup():
    bridge_html = (
        Path(__file__).resolve().parent.parent
        / "components"
        / "device_state_browser_bridge"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert "clearIndexedDBCopies" in bridge_html
    assert ".catch(() => {})" in bridge_html
    assert "indexedDBRef.deleteDatabase(name)" in bridge_html

from pathlib import Path

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
    monkeypatch.setattr(device_state_storage, "_is_native_shell_runtime", lambda: True)

    def fail_component(**kwargs):
        raise AssertionError("native shell should not render the device storage component")

    monkeypatch.setattr(device_state_storage, "_BROWSER_STORAGE_BRIDGE", fail_component)

    restored, ready = sync_device_state_browser_storage(
        {"settings": {"device_save_enabled": True}},
        key="device_state_native_shell_test",
    )

    assert ready is True
    assert restored == {}
    assert device_state_storage.browser_device_storage_available() is False


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

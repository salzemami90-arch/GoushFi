from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import unquote

import streamlit.components.v1 as components


STORAGE_NAME = "goushfi_device_state_storage"
_BROWSER_STORAGE_PENDING = "__PENDING__"
_BROWSER_STORAGE_BRIDGE = components.declare_component(
    "device_state_browser_bridge",
    path=str(Path(__file__).resolve().parent.parent / "components" / "device_state_browser_bridge"),
)


def _encode_payload(payload: dict) -> str:
    if not isinstance(payload, dict):
        return ""
    try:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except Exception:
        return ""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_payload(raw_value: str) -> dict:
    clean_value = unquote(str(raw_value or "").strip())
    if not clean_value:
        return {}
    padding = "=" * (-len(clean_value) % 4)
    try:
        decoded = base64.urlsafe_b64decode((clean_value + padding).encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def browser_device_storage_available() -> bool:
    return True


def sync_device_state_browser_storage(
    payload: dict | None = None,
    *,
    enabled: bool = True,
    clear: bool = False,
    key: str = "device_state_browser_bridge",
) -> tuple[dict, bool]:
    action = "clear" if clear else "read"
    encoded_value = ""
    if not clear and enabled and isinstance(payload, dict):
        encoded_value = _encode_payload(payload)
        if encoded_value:
            action = "sync"

    raw_value = _BROWSER_STORAGE_BRIDGE(
        storageName=STORAGE_NAME,
        value=encoded_value,
        action=action,
        default=_BROWSER_STORAGE_PENDING,
        key=key,
    )
    if raw_value == _BROWSER_STORAGE_PENDING:
        return {}, False

    return _decode_payload(str(raw_value or "")), True

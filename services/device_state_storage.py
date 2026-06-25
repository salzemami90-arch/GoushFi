from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib.parse import unquote

import streamlit as st
import streamlit.components.v1 as components


STORAGE_NAME = "goushfi_device_state_storage"
QUERY_VALUE_PARAM = "f_device_state"
QUERY_READY_PARAM = "f_device_state_ready"
_BROWSER_STORAGE_PENDING = "__PENDING__"
_BROWSER_STORAGE_BRIDGE = components.declare_component(
    "device_state_browser_bridge",
    path=str(Path(__file__).resolve().parent.parent / "components" / "device_state_browser_bridge"),
)


def _is_native_shell_runtime() -> bool:
    try:
        from config_floosy import _is_native_shell_runtime as is_native_shell_runtime

        return bool(is_native_shell_runtime())
    except Exception:
        return False


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


def _query_param_value(name: str) -> str:
    try:
        value = st.query_params.get(name, "")
    except Exception:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "")


def _clear_native_query_params() -> None:
    try:
        if QUERY_VALUE_PARAM in st.query_params:
            del st.query_params[QUERY_VALUE_PARAM]
        if QUERY_READY_PARAM in st.query_params:
            del st.query_params[QUERY_READY_PARAM]
    except Exception:
        return


def _render_native_storage_script(action: str, encoded_value: str = "") -> None:
    storage_name = json.dumps(STORAGE_NAME)
    query_value_param = json.dumps(QUERY_VALUE_PARAM)
    query_ready_param = json.dumps(QUERY_READY_PARAM)
    action_value = json.dumps(str(action or "read"))
    storage_value = json.dumps(str(encoded_value or ""))
    components.html(
        f"""
        <script>
        (function() {{
          const storageName = {storage_name};
          const queryValueParam = {query_value_param};
          const queryReadyParam = {query_ready_param};
          const action = {action_value};
          const desiredValue = {storage_value};

          function collectWindows() {{
            const wins = [];
            let current = window;
            while (current) {{
              if (!wins.includes(current)) wins.push(current);
              let nextWin = null;
              try {{
                nextWin = current.parent && current.parent !== current ? current.parent : null;
              }} catch (error) {{
                nextWin = null;
              }}
              if (!nextWin) break;
              current = nextWin;
            }}
            try {{
              if (window.top && !wins.includes(window.top)) wins.push(window.top);
            }} catch (error) {{}}
            return wins;
          }}

          function getStorage(targetWin) {{
            try {{
              return targetWin && targetWin.localStorage ? targetWin.localStorage : null;
            }} catch (error) {{
              return null;
            }}
          }}

          function collectStorages() {{
            return Array.from(new Set(collectWindows().map((win) => getStorage(win)).filter(Boolean)));
          }}

          function readStoredValue() {{
            for (const storage of collectStorages()) {{
              try {{
                const value = String(storage.getItem(storageName) || "").trim();
                if (value) return value;
              }} catch (error) {{}}
            }}
            return "";
          }}

          function writeStoredValue(value) {{
            for (const storage of collectStorages()) {{
              try {{
                if (value) storage.setItem(storageName, value);
                else storage.removeItem(storageName);
              }} catch (error) {{}}
            }}
          }}

          function clearIndexedDBCopies() {{
            for (const win of collectWindows()) {{
              try {{
                const indexedDBRef = win && win.indexedDB ? win.indexedDB : null;
                if (!indexedDBRef || typeof indexedDBRef.databases !== "function") continue;
                indexedDBRef.databases()
                  .then((databases) => {{
                    if (!Array.isArray(databases)) return;
                    databases.forEach((db) => {{
                      const name = db && db.name ? String(db.name) : "";
                      if (!name) return;
                      try {{
                        indexedDBRef.deleteDatabase(name);
                      }} catch (error) {{}}
                    }});
                  }})
                  .catch(() => {{}});
              }} catch (error) {{}}
            }}
          }}

          function navigateWithStoredValue(value) {{
            const wins = collectWindows().slice().reverse();
            for (const targetWin of wins) {{
              try {{
                if (!targetWin || !targetWin.location) continue;
                const url = new URL(String(targetWin.location.href || ""));
                url.searchParams.set(queryReadyParam, "1");
                if (value) url.searchParams.set(queryValueParam, value);
                else url.searchParams.delete(queryValueParam);
                targetWin.location.replace(url.toString());
                return;
              }} catch (error) {{}}
            }}
          }}

          if (action === "clear") {{
            writeStoredValue("");
            clearIndexedDBCopies();
            return;
          }}

          if (action === "sync") {{
            writeStoredValue(desiredValue);
            return;
          }}

          navigateWithStoredValue(readStoredValue());
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def _sync_native_shell_storage(
    payload: dict | None = None,
    *,
    enabled: bool = True,
    clear: bool = False,
) -> tuple[dict, bool]:
    if clear:
        _render_native_storage_script("clear")
        return {}, True

    if enabled and isinstance(payload, dict):
        encoded_value = _encode_payload(payload)
        _render_native_storage_script("sync", encoded_value)
        return {}, True

    if _query_param_value(QUERY_READY_PARAM) == "1":
        restored = _decode_payload(_query_param_value(QUERY_VALUE_PARAM))
        _clear_native_query_params()
        return restored, True

    _render_native_storage_script("read")
    return {}, False


def sync_device_state_browser_storage(
    payload: dict | None = None,
    *,
    enabled: bool = True,
    clear: bool = False,
    key: str = "device_state_browser_bridge",
) -> tuple[dict, bool]:
    if _is_native_shell_runtime():
        return _sync_native_shell_storage(payload, enabled=enabled, clear=clear)

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

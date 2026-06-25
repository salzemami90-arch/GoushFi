from __future__ import annotations

import base64
import json
import os
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import unquote

import streamlit as st
import streamlit.components.v1 as components
from services.local_store import delete_sqlite_payload, load_sqlite_payload, save_sqlite_payload


COOKIE_NAME = "floosy_cloud_auth"
STORAGE_NAME = "floosy_cloud_auth_storage"
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30
LOCAL_AUTH_SQLITE_FILE = os.path.join("data", "floosy_cloud_auth.sqlite3")
_BROWSER_STORAGE_PENDING = "__PENDING__"
_BROWSER_STORAGE_BRIDGE = components.declare_component(
    "cloud_auth_browser_bridge",
    path=str(Path(__file__).resolve().parent.parent / "components" / "cloud_auth_browser_bridge"),
)


def _is_native_shell_runtime() -> bool:
    try:
        from config_floosy import _is_native_shell_runtime as is_native_shell_runtime

        return bool(is_native_shell_runtime())
    except Exception:
        return False


def _encode_payload(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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


def _extract_auth_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if not refresh_token:
        return {}
    return {
        "email": str(payload.get("email") or "").strip(),
        "user_id": str(payload.get("user_id") or "").strip(),
        "refresh_token": refresh_token,
    }


def _runtime_url() -> str:
    try:
        context = getattr(st, "context", None)
    except Exception:
        context = None

    if context is None:
        return ""

    try:
        runtime_url = str(getattr(context, "url", "") or "")
    except Exception:
        runtime_url = ""

    if runtime_url:
        return runtime_url

    try:
        headers = getattr(context, "headers", {}) or {}
    except Exception:
        headers = {}

    if hasattr(headers, "get"):
        host = str(headers.get("host") or headers.get("Host") or "").strip()
    elif isinstance(headers, dict):
        host = str(headers.get("host") or headers.get("Host") or "").strip()
    else:
        host = ""

    return f"http://{host}" if host else ""


def _is_local_runtime() -> bool:
    try:
        from config_floosy import _local_persistence_enabled  # lazy import to avoid startup cycles

        if bool(_local_persistence_enabled()):
            return True
    except Exception:
        pass

    runtime_url = _runtime_url().strip().lower()
    return "://localhost" in runtime_url or "://127.0.0.1" in runtime_url or "://0.0.0.0" in runtime_url


def _local_auth_backup_exists() -> bool:
    return Path(LOCAL_AUTH_SQLITE_FILE).exists()


def _read_local_auth_backup() -> dict:
    try:
        context = getattr(st, "context", None)
    except Exception:
        context = None

    has_runtime_url_attr = bool(context is not None and hasattr(context, "url"))
    runtime_url = _runtime_url().strip().lower()
    allow_local_backup = _is_local_runtime()
    if not allow_local_backup and has_runtime_url_attr and not runtime_url and _local_auth_backup_exists():
        allow_local_backup = True

    if not allow_local_backup:
        return {}
    payload = load_sqlite_payload(LOCAL_AUTH_SQLITE_FILE)
    return _extract_auth_payload(payload if isinstance(payload, dict) else {})


def _write_local_auth_backup(payload: dict) -> None:
    if not _is_local_runtime():
        return
    normalized = _extract_auth_payload(payload)
    if not normalized:
        return
    save_sqlite_payload(LOCAL_AUTH_SQLITE_FILE, normalized)


def _clear_local_auth_backup() -> None:
    if not _is_local_runtime():
        return
    delete_sqlite_payload(LOCAL_AUTH_SQLITE_FILE)


def read_cloud_auth_cookie() -> dict:
    raw_value = ""
    try:
        cookies = getattr(st.context, "cookies", {})
        raw_value = cookies.get(COOKIE_NAME, "") if cookies is not None else ""
    except Exception:
        raw_value = ""

    if not raw_value:
        try:
            headers = getattr(st.context, "headers", {}) or {}
        except Exception:
            headers = {}

        raw_cookie_header = ""
        if hasattr(headers, "get"):
            raw_cookie_header = str(headers.get("cookie") or headers.get("Cookie") or "").strip()
        elif isinstance(headers, dict):
            normalized_headers = {str(k).lower(): v for k, v in headers.items()}
            raw_cookie_header = str(normalized_headers.get("cookie") or "").strip()

        if raw_cookie_header:
            parsed = SimpleCookie()
            try:
                parsed.load(raw_cookie_header)
            except Exception:
                parsed = SimpleCookie()
            morsel = parsed.get(COOKIE_NAME)
            if morsel is not None:
                raw_value = morsel.value

    payload = _extract_auth_payload(_decode_payload(raw_value))
    if payload:
        return payload
    return _read_local_auth_backup()


def sync_cloud_auth_browser_storage(payload: dict | None = None, *, clear: bool = False) -> tuple[dict, bool]:
    if _is_native_shell_runtime():
        return {}, True

    encoded_value = ""
    if not clear and isinstance(payload, dict):
        normalized_payload = _extract_auth_payload(payload)
        if normalized_payload:
            encoded_value = _encode_payload(normalized_payload)

    raw_value = _BROWSER_STORAGE_BRIDGE(
        storageName=STORAGE_NAME,
        value=encoded_value,
        action="clear" if clear else "sync",
        default=_BROWSER_STORAGE_PENDING,
        key="cloud_auth_browser_bridge",
    )
    if raw_value == _BROWSER_STORAGE_PENDING:
        return {}, False

    return _extract_auth_payload(_decode_payload(str(raw_value or ""))), True


def _render_cookie_script(value: str, max_age: int, *, reload_after_write: bool = False) -> None:
    cookie_name = json.dumps(COOKIE_NAME)
    storage_name = json.dumps(STORAGE_NAME)
    cookie_value = json.dumps(value)
    cookie_max_age = int(max_age)
    components.html(
        f"""
        <script>
        (function() {{
          const name = {cookie_name};
          const value = encodeURIComponent({cookie_value});
          const maxAge = {cookie_max_age};
          const storageName = {storage_name};
          const signedOutFlag = `${{storageName}}_signed_out`;
          const expiry = maxAge === 0 ? "; expires=Thu, 01 Jan 1970 00:00:00 GMT" : "";
          const shouldReloadAfterWrite = {"true" if reload_after_write else "false"};

          function collectWindows() {{
            const wins = [];
            let current = window;
            while (current) {{
              if (!wins.includes(current)) {{
                wins.push(current);
              }}
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
              if (window.top && !wins.includes(window.top)) {{
                wins.push(window.top);
              }}
            }} catch (error) {{}}
            return wins;
          }}

          function safeHostname(win) {{
            try {{
              return String((win && win.location && win.location.hostname) || "").trim();
            }} catch (error) {{
              return "";
            }}
          }}

          function safeProtocol(win) {{
            try {{
              return String((win && win.location && win.location.protocol) || "").trim().toLowerCase();
            }} catch (error) {{
              return "";
            }}
          }}

          function writeCookie(targetDoc, cookieString) {{
            if (!targetDoc) return;
            try {{
              targetDoc.cookie = cookieString;
            }} catch (error) {{}}
          }}

          function getStorage(targetWin) {{
            try {{
              return targetWin && targetWin.localStorage ? targetWin.localStorage : null;
            }} catch (error) {{
              return null;
            }}
          }}

          const wins = collectWindows();
          const hostnames = Array.from(new Set(wins.map((win) => safeHostname(win)).filter(Boolean)));

          const protocol = wins.map((win) => safeProtocol(win)).find(Boolean) || safeProtocol(window);
          const isHttps = protocol === "https:";
          const baseAttrs = `path=/; max-age=${{maxAge}}${{expiry}}`;
          const variants = [
            `${{name}}=${{value}}; ${{baseAttrs}}; SameSite=Lax${{isHttps ? "; Secure" : ""}}`,
          ];

          if (isHttps) {{
            variants.push(
              `${{name}}=${{value}}; ${{baseAttrs}}; SameSite=None; Secure`,
              `${{name}}=${{value}}; ${{baseAttrs}}; SameSite=None; Secure; Partitioned`,
            );
          }}

          const targets = Array.from(new Set(wins.map((win) => {{
            try {{
              return win && win.document ? win.document : null;
            }} catch (error) {{
              return null;
            }}
          }}).filter(Boolean)));

          for (const targetDoc of targets) {{
            for (const variant of variants) {{
              writeCookie(targetDoc, variant);
              for (const hostname of hostnames) {{
                if (hostname.includes(".")) {{
                  writeCookie(targetDoc, `${{variant}}; domain=${{hostname}}`);
                }}
              }}
            }}
          }}

          const storages = Array.from(new Set(wins.map((win) => getStorage(win)).filter(Boolean)));

          for (const storage of storages) {{
            try {{
              if ({cookie_max_age} > 0 && {cookie_value}) {{
                storage.removeItem(signedOutFlag);
                storage.setItem(storageName, {cookie_value});
              }} else {{
                storage.removeItem(storageName);
                storage.setItem(signedOutFlag, String(Date.now()));
              }}
            }} catch (error) {{}}
          }}

          if (shouldReloadAfterWrite) {{
            window.setTimeout(() => {{
              try {{
                window.location.replace(String(window.location.href || ""));
                return;
              }} catch (error) {{}}
              try {{
                window.location.reload();
              }} catch (error) {{}}
            }}, 40);
          }}
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def render_cloud_oauth_callback_capture() -> dict:
    if _is_native_shell_runtime():
        return {}

    raw_value = _BROWSER_STORAGE_BRIDGE(
        storageName=STORAGE_NAME,
        value="",
        action="capture_oauth",
        default=_BROWSER_STORAGE_PENDING,
        key="cloud_oauth_callback_bridge",
    )
    if raw_value == _BROWSER_STORAGE_PENDING:
        return {}

    payload = _extract_auth_payload(_decode_payload(str(raw_value or "")))
    if not payload:
        return {}

    _write_local_auth_backup(payload)
    st.session_state["_cloud_cookie_restore_checked"] = False
    _render_cookie_script(
        _encode_payload(payload),
        COOKIE_MAX_AGE_SECONDS,
        reload_after_write=True,
    )
    return payload


def render_cloud_oauth_hash_capture_inline() -> None:
    cookie_name = json.dumps(COOKIE_NAME)
    storage_name = json.dumps(STORAGE_NAME)
    cookie_max_age = int(COOKIE_MAX_AGE_SECONDS)
    components.html(
        f"""
        <script>
        (function() {{
          function collectWindows() {{
            const wins = [];
            let current = window;
            while (current) {{
              if (!wins.includes(current)) {{
                wins.push(current);
              }}
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
              if (window.top && !wins.includes(window.top)) {{
                wins.push(window.top);
              }}
            }} catch (error) {{}}
            return wins;
          }}

          function windowHash(targetWin) {{
            try {{
              return String((targetWin && targetWin.location && targetWin.location.hash) || "");
            }} catch (error) {{
              return "";
            }}
          }}

          const wins = collectWindows();
          const sourceWin = wins.find((targetWin) => /(?:^#|&)refresh_token=/.test(windowHash(targetWin)));
          const hashValue = windowHash(sourceWin || window);
          if (!/(?:^#|&)refresh_token=/.test(hashValue)) {{
            return;
          }}

          const cookieName = {cookie_name};
          const storageName = {storage_name};
          const maxAge = {cookie_max_age};
          const params = new URLSearchParams(hashValue.replace(/^#/, ""));
          const refreshToken = String(params.get("refresh_token") || "").trim();
          if (!refreshToken) {{
            return;
          }}

          function base64UrlEncode(text) {{
            let bytes = [];
            try {{
              bytes = Array.from(new TextEncoder().encode(text));
            }} catch (error) {{
              const encoded = unescape(encodeURIComponent(text));
              bytes = Array.from(encoded).map((char) => char.charCodeAt(0));
            }}
            let binary = "";
            for (const byte of bytes) {{
              binary += String.fromCharCode(byte);
            }}
            return btoa(binary).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/g, "");
          }}

          function base64UrlDecode(text) {{
            const base64 = String(text || "").replace(/-/g, "+").replace(/_/g, "/");
            const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
            try {{
              const binary = atob(padded);
              const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
              return new TextDecoder().decode(bytes);
            }} catch (error) {{
              try {{
                return decodeURIComponent(escape(atob(padded)));
              }} catch (nestedError) {{
                return "";
              }}
            }}
          }}

          function decodeJwtPayload(token) {{
            try {{
              const payloadPart = String(token || "").split(".")[1] || "";
              if (!payloadPart) {{
                return {{}};
              }}
              return JSON.parse(base64UrlDecode(payloadPart));
            }} catch (error) {{
              return {{}};
            }}
          }}

          const claims = decodeJwtPayload(String(params.get("access_token") || "").trim());
          const encodedPayload = base64UrlEncode(JSON.stringify({{
            email: String(claims.email || ""),
            user_id: String(claims.sub || ""),
            refresh_token: refreshToken,
          }}));

          const storages = Array.from(new Set(wins.map((targetWin) => {{
            try {{
              return targetWin && targetWin.localStorage ? targetWin.localStorage : null;
            }} catch (error) {{
              return null;
            }}
          }}).filter(Boolean)));

          for (const storage of storages) {{
            try {{
              storage.setItem(storageName, encodedPayload);
            }} catch (error) {{}}
          }}

          const isHttps = wins.some((targetWin) => {{
            try {{
              return String(targetWin.location.protocol || "").toLowerCase() === "https:";
            }} catch (error) {{
              return false;
            }}
          }});
          const baseAttrs = `path=/; max-age=${{maxAge}}`;
          const variants = [
            `${{cookieName}}=${{encodeURIComponent(encodedPayload)}}; ${{baseAttrs}}; SameSite=Lax${{isHttps ? "; Secure" : ""}}`,
          ];
          if (isHttps) {{
            variants.push(
              `${{cookieName}}=${{encodeURIComponent(encodedPayload)}}; ${{baseAttrs}}; SameSite=None; Secure`,
              `${{cookieName}}=${{encodeURIComponent(encodedPayload)}}; ${{baseAttrs}}; SameSite=None; Secure; Partitioned`,
            );
          }}

          const docs = Array.from(new Set(wins.map((targetWin) => {{
            try {{
              return targetWin && targetWin.document ? targetWin.document : null;
            }} catch (error) {{
              return null;
            }}
          }}).filter(Boolean)));

          for (const targetDoc of docs) {{
            for (const variant of variants) {{
              try {{
                targetDoc.cookie = variant;
              }} catch (error) {{}}
            }}
          }}

          const targetWin = sourceWin || window;
          try {{
            const cleanUrl = `${{targetWin.location.origin}}${{targetWin.location.pathname}}${{targetWin.location.search}}`;
            targetWin.history.replaceState(null, targetWin.document ? targetWin.document.title : "", cleanUrl);
          }} catch (error) {{
            try {{
              targetWin.location.hash = "";
            }} catch (nestedError) {{}}
          }}

          window.setTimeout(() => {{
            try {{
              targetWin.location.reload();
            }} catch (error) {{}}
          }}, 80);
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def remember_cloud_auth(email: str, user_id: str, refresh_token: str, *, reload_after_write: bool = False) -> None:
    clean_refresh_token = str(refresh_token or "").strip()
    if not clean_refresh_token:
        return
    payload = {
        "email": str(email or "").strip(),
        "user_id": str(user_id or "").strip(),
        "refresh_token": clean_refresh_token,
    }
    _write_local_auth_backup(payload)
    value = _encode_payload(payload)
    _render_cookie_script(value, COOKIE_MAX_AGE_SECONDS, reload_after_write=reload_after_write)


def clear_cloud_auth_cookie(*, reload_after_write: bool = False) -> None:
    _clear_local_auth_backup()
    _render_cookie_script("", 0, reload_after_write=reload_after_write)


def bootstrap_cloud_auth_from_storage() -> None:
    cookie_name = json.dumps(COOKIE_NAME)
    storage_name = json.dumps(STORAGE_NAME)
    cookie_max_age = int(COOKIE_MAX_AGE_SECONDS)
    components.html(
        f"""
        <script>
        (function() {{
          const cookieName = {cookie_name};
          const storageName = {storage_name};
          const maxAge = {cookie_max_age};
          const bootFlag = `${{storageName}}_bootstrap_done`;
          const signedOutFlag = `${{storageName}}_signed_out`;

          function collectWindows() {{
            const wins = [];
            let current = window;
            while (current) {{
              if (!wins.includes(current)) {{
                wins.push(current);
              }}
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
              if (window.top && !wins.includes(window.top)) {{
                wins.push(window.top);
              }}
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

          function getSessionStorage(targetWin) {{
            try {{
              return targetWin && targetWin.sessionStorage ? targetWin.sessionStorage : null;
            }} catch (error) {{
              return null;
            }}
          }}

          function readCookie(targetDoc, name) {{
            if (!targetDoc || !targetDoc.cookie) return "";
            const prefix = `${{name}}=`;
            const parts = String(targetDoc.cookie).split(";").map((part) => part.trim());
            for (const part of parts) {{
              if (part.startsWith(prefix)) {{
                return part.slice(prefix.length);
              }}
            }}
            return "";
          }}

          function writeCookie(targetDoc, cookieString) {{
            if (!targetDoc) return;
            try {{
              targetDoc.cookie = cookieString;
            }} catch (error) {{}}
          }}

          function safeHostname(targetWin) {{
            try {{
              return String((targetWin && targetWin.location && targetWin.location.hostname) || "").trim();
            }} catch (error) {{
              return "";
            }}
          }}

          function safeProtocol(targetWin) {{
            try {{
              return String((targetWin && targetWin.location && targetWin.location.protocol) || "").trim().toLowerCase();
            }} catch (error) {{
              return "";
            }}
          }}
          const wins = collectWindows();
          const storages = Array.from(new Set(wins.map((win) => getStorage(win)).filter(Boolean)));
          const sessionStores = Array.from(new Set(wins.map((win) => getSessionStorage(win)).filter(Boolean)));
          const docs = Array.from(new Set(wins.map((win) => {{
            try {{
              return win && win.document ? win.document : null;
            }} catch (error) {{
              return null;
            }}
          }}).filter(Boolean)));

          const cookieExists = docs.some((targetDoc) => !!readCookie(targetDoc, cookieName));
          if (cookieExists) {{
            for (const store of sessionStores) {{
              try {{
                store.removeItem(bootFlag);
              }} catch (error) {{}}
            }}
            return;
          }}

          const signedOut = storages.some((storage) => {{
            try {{
              return !!String(storage.getItem(signedOutFlag) || "").trim();
            }} catch (error) {{
              return false;
            }}
          }});
          if (signedOut) {{
            for (const storage of storages) {{
              try {{
                storage.removeItem(storageName);
              }} catch (error) {{}}
            }}
            for (const store of sessionStores) {{
              try {{
                store.removeItem(bootFlag);
              }} catch (error) {{}}
            }}
            return;
          }}

          let storedValue = "";
          for (const storage of storages) {{
            try {{
              storedValue = String(storage.getItem(storageName) || "").trim();
            }} catch (error) {{
              storedValue = "";
            }}
            if (storedValue) break;
          }}

          if (!storedValue) {{
            for (const store of sessionStores) {{
              try {{
                store.removeItem(bootFlag);
              }} catch (error) {{}}
            }}
            return;
          }}

          const alreadyBootstrapped = sessionStores.some((store) => {{
            try {{
              return store.getItem(bootFlag) === storedValue;
            }} catch (error) {{
              return false;
            }}
          }});
          if (alreadyBootstrapped) {{
            return;
          }}

          const protocol = wins.map((win) => safeProtocol(win)).find(Boolean) || safeProtocol(window);
          const isHttps = protocol === "https:";
          const expiry = "";
          const baseAttrs = `path=/; max-age=${{maxAge}}${{expiry}}`;
          const variants = [
            `${{cookieName}}=${{encodeURIComponent(storedValue)}}; ${{baseAttrs}}; SameSite=Lax${{isHttps ? "; Secure" : ""}}`,
          ];

          if (isHttps) {{
            variants.push(
              `${{cookieName}}=${{encodeURIComponent(storedValue)}}; ${{baseAttrs}}; SameSite=None; Secure`,
              `${{cookieName}}=${{encodeURIComponent(storedValue)}}; ${{baseAttrs}}; SameSite=None; Secure; Partitioned`,
            );
          }}

          const hostnames = Array.from(new Set(wins.map((win) => safeHostname(win)).filter(Boolean)));

          for (const targetDoc of docs) {{
            for (const variant of variants) {{
              writeCookie(targetDoc, variant);
              for (const hostname of hostnames) {{
                if (hostname.includes(".")) {{
                  writeCookie(targetDoc, `${{variant}}; domain=${{hostname}}`);
                }}
              }}
            }}
          }}

          for (const store of sessionStores) {{
            try {{
              store.setItem(bootFlag, storedValue);
            }} catch (error) {{}}
          }}

          const reloadTargets = wins.slice().reverse();
          for (const targetWin of reloadTargets) {{
            try {{
              if (targetWin && targetWin.location) {{
                targetWin.location.replace(String(targetWin.location.href || ""));
                return;
              }}
            }} catch (error) {{}}
          }}

          try {{
            window.location.reload();
          }} catch (error) {{}}
        }})();
        </script>
        """,
        height=0,
        width=0,
    )

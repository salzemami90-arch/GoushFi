from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie
from urllib.parse import unquote

import streamlit as st
import streamlit.components.v1 as components


_PKCE_TTL_SECONDS = 15 * 60
_PKCE_STORE: dict[str, tuple[str, float]] = {}
PKCE_COOKIE_NAME = "floosy_cloud_oauth_pkce"
PKCE_SESSION_STATE_KEY = "_cloud_oauth_pkce_flow"
PKCE_SQLITE_FILE = os.getenv("FLOOSY_PKCE_SQLITE_FILE", os.path.join("data", "floosy_oauth_pkce.sqlite3"))


def _code_challenge_for_verifier(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _prune_expired(now: float) -> None:
    expired_states = [
        state for state, (_, expires_at) in _PKCE_STORE.items() if expires_at <= now
    ]
    for state in expired_states:
        _PKCE_STORE.pop(state, None)
    _delete_expired_durable_flows(now)


def _connect_store() -> sqlite3.Connection:
    base_dir = os.path.dirname(PKCE_SQLITE_FILE)
    if base_dir:
        os.makedirs(base_dir, exist_ok=True)

    conn = sqlite3.connect(PKCE_SQLITE_FILE)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS oauth_pkce_flows (
            state TEXT PRIMARY KEY,
            code_verifier TEXT NOT NULL,
            expires_at REAL NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    return conn


def _save_durable_flow(flow: dict) -> None:
    normalized = _normalized_flow(flow)
    if not normalized:
        return

    try:
        with _connect_store() as conn:
            conn.execute(
                """
                REPLACE INTO oauth_pkce_flows (state, code_verifier, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    normalized["state"],
                    normalized["code_verifier"],
                    float(normalized["expires_at"]),
                    time.time(),
                ),
            )
            conn.commit()
    except Exception:
        return


def _load_durable_flow(state: str) -> dict[str, str]:
    clean_state = str(state or "").strip()
    if not clean_state:
        return {}
    if not os.path.exists(PKCE_SQLITE_FILE):
        return {}

    now = time.time()
    try:
        with _connect_store() as conn:
            row = conn.execute(
                """
                SELECT state, code_verifier, expires_at
                FROM oauth_pkce_flows
                WHERE state = ?
                """,
                (clean_state,),
            ).fetchone()
    except Exception:
        return {}

    if not row:
        return {}

    return _normalized_flow(
        {
            "state": row[0],
            "code_verifier": row[1],
            "expires_at": row[2],
        },
        now=now,
    )


def _delete_durable_flow(state: str) -> None:
    clean_state = str(state or "").strip()
    if not clean_state:
        return
    if not os.path.exists(PKCE_SQLITE_FILE):
        return

    try:
        with _connect_store() as conn:
            conn.execute("DELETE FROM oauth_pkce_flows WHERE state = ?", (clean_state,))
            conn.commit()
    except Exception:
        return


def _delete_expired_durable_flows(now: float) -> None:
    if not os.path.exists(PKCE_SQLITE_FILE):
        return

    try:
        with _connect_store() as conn:
            conn.execute("DELETE FROM oauth_pkce_flows WHERE expires_at <= ?", (float(now),))
            conn.commit()
    except Exception:
        return


def create_pkce_flow() -> dict[str, str]:
    now = time.time()
    _prune_expired(now)

    code_verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(24)
    expires_at = now + _PKCE_TTL_SECONDS
    _PKCE_STORE[state] = (code_verifier, expires_at)

    return {
        "state": state,
        "code_verifier": code_verifier,
        "code_challenge": _code_challenge_for_verifier(code_verifier),
        "code_challenge_method": "S256",
        "expires_at": str(expires_at),
    }


def _expires_at(flow: dict) -> float:
    try:
        return float(flow.get("expires_at") or 0)
    except Exception:
        return 0.0


def _normalized_flow(flow: dict | None, now: float | None = None) -> dict[str, str]:
    if not isinstance(flow, dict):
        return {}

    now_value = time.time() if now is None else float(now)
    state = str(flow.get("state") or "").strip()
    code_verifier = str(flow.get("code_verifier") or "").strip()
    expires_at = _expires_at(flow)
    if not state or not code_verifier or expires_at <= now_value:
        return {}

    code_challenge = str(flow.get("code_challenge") or "").strip()
    if not code_challenge:
        code_challenge = _code_challenge_for_verifier(code_verifier)

    return {
        "state": state,
        "code_verifier": code_verifier,
        "code_challenge": code_challenge,
        "code_challenge_method": str(flow.get("code_challenge_method") or "S256").strip() or "S256",
        "expires_at": str(expires_at),
    }


def remember_pkce_flow(session_state, flow: dict) -> dict[str, str]:
    normalized = _normalized_flow(flow)
    if not normalized:
        return {}

    session_state[PKCE_SESSION_STATE_KEY] = dict(normalized)
    session_state["_cloud_oauth_pkce_state"] = normalized["state"]
    session_state["_cloud_oauth_pkce_verifier"] = normalized["code_verifier"]
    session_state["_cloud_oauth_pkce_expires_at"] = normalized["expires_at"]
    _PKCE_STORE[normalized["state"]] = (
        normalized["code_verifier"],
        float(normalized["expires_at"]),
    )
    _save_durable_flow(normalized)
    return normalized


def get_or_create_pkce_flow(session_state) -> dict[str, str]:
    now = time.time()
    existing = _normalized_flow(session_state.get(PKCE_SESSION_STATE_KEY), now=now)
    if not existing:
        legacy_flow = {
            "state": session_state.get("_cloud_oauth_pkce_state", ""),
            "code_verifier": session_state.get("_cloud_oauth_pkce_verifier", ""),
            "expires_at": session_state.get("_cloud_oauth_pkce_expires_at", ""),
        }
        existing = _normalized_flow(legacy_flow, now=now)

    if existing:
        return remember_pkce_flow(session_state, existing)

    return remember_pkce_flow(session_state, create_pkce_flow())


def forget_pkce_flow(session_state) -> None:
    flow = session_state.get(PKCE_SESSION_STATE_KEY)
    state = ""
    if isinstance(flow, dict):
        state = str(flow.get("state") or "")
    state = state or str(session_state.get("_cloud_oauth_pkce_state") or "")
    if state:
        _PKCE_STORE.pop(state, None)
        _delete_durable_flow(state)

    session_state.pop(PKCE_SESSION_STATE_KEY, None)
    session_state.pop("_cloud_oauth_pkce_state", None)
    session_state.pop("_cloud_oauth_pkce_verifier", None)
    session_state.pop("_cloud_oauth_pkce_expires_at", None)


def pop_pkce_verifier(state: str) -> str:
    clean_state = str(state or "").strip()
    if not clean_state:
        return ""

    now = time.time()
    _prune_expired(now)
    record = _PKCE_STORE.pop(clean_state, None)
    if not record:
        durable_flow = _load_durable_flow(clean_state)
        if durable_flow:
            _delete_durable_flow(clean_state)
            return durable_flow["code_verifier"]
        return ""

    code_verifier, expires_at = record
    if expires_at <= now:
        return ""
    _delete_durable_flow(clean_state)
    return code_verifier


def _lookup_pkce_verifier(state: str) -> str:
    clean_state = str(state or "").strip()
    if not clean_state:
        return ""

    now = time.time()
    _prune_expired(now)
    record = _PKCE_STORE.get(clean_state)
    if record:
        code_verifier, expires_at = record
        if expires_at > now:
            return code_verifier

    durable_flow = _load_durable_flow(clean_state)
    if durable_flow:
        _PKCE_STORE[durable_flow["state"]] = (
            durable_flow["code_verifier"],
            float(durable_flow["expires_at"]),
        )
        return durable_flow["code_verifier"]

    return ""


def _encode_cookie_flow(flow: dict) -> str:
    normalized = _normalized_flow(flow)
    if not normalized:
        return ""

    raw = json.dumps(
        {
            "state": normalized["state"],
            "code_verifier": normalized["code_verifier"],
            "expires_at": normalized["expires_at"],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cookie_flow(raw_value: str, now: float | None = None) -> dict[str, str]:
    clean_value = unquote(str(raw_value or "").strip())
    if not clean_value:
        return {}
    padding = "=" * (-len(clean_value) % 4)
    try:
        decoded = base64.urlsafe_b64decode((clean_value + padding).encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}
    return _normalized_flow(payload if isinstance(payload, dict) else {}, now=now)


def read_pkce_cookie(context=None) -> dict[str, str]:
    if context is None:
        try:
            context = getattr(st, "context", None)
        except Exception:
            context = None

    raw_value = ""
    if context is not None:
        try:
            cookies = getattr(context, "cookies", {}) or {}
            if hasattr(cookies, "get"):
                raw_value = str(cookies.get(PKCE_COOKIE_NAME) or "")
        except Exception:
            raw_value = ""

        if not raw_value:
            try:
                headers = getattr(context, "headers", {}) or {}
            except Exception:
                headers = {}

            raw_cookie_header = ""
            if hasattr(headers, "get"):
                raw_cookie_header = str(headers.get("cookie") or headers.get("Cookie") or "").strip()
            elif isinstance(headers, dict):
                normalized_headers = {str(key).lower(): value for key, value in headers.items()}
                raw_cookie_header = str(normalized_headers.get("cookie") or "").strip()

            if raw_cookie_header:
                parsed = SimpleCookie()
                try:
                    parsed.load(raw_cookie_header)
                except Exception:
                    parsed = SimpleCookie()
                morsel = parsed.get(PKCE_COOKIE_NAME)
                if morsel is not None:
                    raw_value = morsel.value

    return _decode_cookie_flow(raw_value)


def _verifier_from_flow(flow: dict | None, state: str = "", *, require_state_match: bool = True) -> str:
    normalized = _normalized_flow(flow)
    if not normalized:
        return ""

    clean_state = str(state or "").strip()
    if require_state_match and clean_state and normalized["state"] != clean_state:
        return ""
    return normalized["code_verifier"]


def resolve_pkce_verifier(
    state: str = "",
    *,
    cookie_flow: dict | None = None,
    session_state=None,
) -> str:
    clean_state = str(state or "").strip()
    if clean_state:
        verifier = _lookup_pkce_verifier(clean_state)
        if verifier:
            return verifier

    session_flow = {}
    if session_state is not None:
        session_flow = session_state.get(PKCE_SESSION_STATE_KEY)
        if not isinstance(session_flow, dict):
            session_flow = {
                "state": session_state.get("_cloud_oauth_pkce_state", ""),
                "code_verifier": session_state.get("_cloud_oauth_pkce_verifier", ""),
                "expires_at": session_state.get("_cloud_oauth_pkce_expires_at", ""),
            }

    if clean_state:
        for flow in (cookie_flow, session_flow):
            verifier = _verifier_from_flow(flow, clean_state)
            if verifier:
                return verifier

    for flow in (cookie_flow, session_flow):
        verifier = _verifier_from_flow(flow, "", require_state_match=False)
        if verifier:
            return verifier

    return ""


def render_pkce_cookie(flow: dict) -> None:
    encoded_value = _encode_cookie_flow(flow)
    if not encoded_value:
        return

    cookie_name = json.dumps(PKCE_COOKIE_NAME)
    cookie_value = json.dumps(encoded_value)
    cookie_max_age = int(_PKCE_TTL_SECONDS)
    components.html(
        f"""
        <script>
        (function() {{
          const name = {cookie_name};
          const value = encodeURIComponent({cookie_value});
          const maxAge = {cookie_max_age};

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

          const wins = collectWindows();
          const protocol = wins.map((win) => safeProtocol(win)).find(Boolean) || safeProtocol(window);
          const isHttps = protocol === "https:";
          const baseAttrs = `path=/; max-age=${{maxAge}}`;
          const variants = [
            `${{name}}=${{value}}; ${{baseAttrs}}; SameSite=Lax${{isHttps ? "; Secure" : ""}}`,
          ];

          if (isHttps) {{
            variants.push(`${{name}}=${{value}}; ${{baseAttrs}}; SameSite=None; Secure`);
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
            }}
          }}
        }})();
        </script>
        """,
        height=0,
        width=0,
    )

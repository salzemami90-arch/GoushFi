from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

import requests


def _runtime_value(source: Any, *keys: str) -> str:
    if source is None:
        return ""

    for key in keys:
        value = None
        try:
            if hasattr(source, "get"):
                value = source.get(key, None)
        except Exception:
            value = None

        if value is None:
            try:
                value = source[key]
            except Exception:
                value = None

        if value is None:
            try:
                value = getattr(source, key)
            except Exception:
                value = None

        if value is not None:
            clean_value = str(value or "").strip()
            if clean_value:
                return clean_value

    return ""


def _runtime_section(source: Any, *keys: str) -> Any:
    if source is None:
        return None

    for key in keys:
        value = None
        try:
            if hasattr(source, "get"):
                value = source.get(key, None)
        except Exception:
            value = None

        if value is None:
            try:
                value = source[key]
            except Exception:
                value = None

        if value is None:
            try:
                value = getattr(source, key)
            except Exception:
                value = None

        if value is not None:
            return value

    return None


class SupabaseSyncClient:
    def __init__(self, supabase_url: str, anon_key: str, table_name: str = "user_app_data", timeout_sec: int = 15):
        self.supabase_url = (supabase_url or "").strip().rstrip("/")
        self.anon_key = (anon_key or "").strip()
        self.table_name = table_name
        self.timeout_sec = timeout_sec

    @classmethod
    def from_runtime(cls, secrets: Any = None) -> "SupabaseSyncClient":
        secret_url = ""
        secret_key = ""
        secret_table_name = ""

        if secrets is not None:
            secret_sources = [secrets]
            supabase_section = _runtime_section(secrets, "supabase", "SUPABASE")
            cloud_section = _runtime_section(secrets, "cloud", "CLOUD")
            connections_section = _runtime_section(secrets, "connections", "CONNECTIONS")
            connection_supabase_section = _runtime_section(
                connections_section, "supabase", "SUPABASE"
            )
            for source in [supabase_section, cloud_section, connection_supabase_section]:
                if source is not None:
                    secret_sources.append(source)

            for source in secret_sources:
                if not secret_url:
                    secret_url = _runtime_value(
                        source,
                        "SUPABASE_URL",
                        "supabase_url",
                        "url",
                        "project_url",
                    )
                if not secret_key:
                    secret_key = _runtime_value(
                        source,
                        "SUPABASE_ANON_KEY",
                        "supabase_anon_key",
                        "anon_key",
                        "api_key",
                    )
                if not secret_table_name:
                    secret_table_name = _runtime_value(
                        source,
                        "SUPABASE_DATA_TABLE",
                        "supabase_data_table",
                        "data_table",
                        "table_name",
                        "table",
                    )

        url = secret_url or os.getenv("SUPABASE_URL", "")
        key = secret_key or os.getenv("SUPABASE_ANON_KEY", "")
        table_name = secret_table_name or os.getenv("SUPABASE_DATA_TABLE", "user_app_data")
        return cls(url, key, table_name=table_name)

    @property
    def is_configured(self) -> bool:
        return bool(self.supabase_url and self.anon_key)

    def build_oauth_authorize_url(
        self,
        provider: str,
        redirect_to: str,
        *,
        scopes: str = "",
        code_challenge: str = "",
        code_challenge_method: str = "",
        state: str = "",
    ) -> str:
        if not self.is_configured:
            return ""

        clean_provider = str(provider or "").strip().lower()
        if clean_provider not in {"apple", "google"}:
            return ""

        params = {"provider": clean_provider}
        clean_redirect = str(redirect_to or "").strip()
        if clean_redirect:
            params["redirect_to"] = clean_redirect

        clean_scopes = str(scopes or "").strip()
        if clean_scopes:
            params["scopes"] = clean_scopes

        clean_code_challenge = str(code_challenge or "").strip()
        if clean_code_challenge:
            params["code_challenge"] = clean_code_challenge
            params["flow_type"] = "pkce"

        clean_code_challenge_method = str(code_challenge_method or "").strip()
        if clean_code_challenge_method:
            params["code_challenge_method"] = clean_code_challenge_method

        clean_state = str(state or "").strip()
        if clean_state:
            params["state"] = clean_state

        return f"{self.supabase_url}/auth/v1/authorize?{urlencode(params)}"

    def _headers(self, access_token: str | None = None, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.anon_key,
            "Content-Type": "application/json",
        }
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        if prefer:
            headers["Prefer"] = prefer
        return headers

    @staticmethod
    def _json_or_text(resp: requests.Response) -> Any:
        try:
            return resp.json()
        except Exception:
            return resp.text

    @staticmethod
    def _friendly_error(resp: requests.Response, data: Any, keys: tuple[str, ...]) -> str:
        status_code = int(getattr(resp, "status_code", 0) or 0)
        if isinstance(data, dict):
            for key in keys:
                value = str(data.get(key) or "").strip()
                if value:
                    return value
            return str(data)

        message = str(data or "").strip()
        message_lower = message.lower()
        if (
            status_code in {521, 522, 523, 524}
            or "<html" in message_lower
            or "<!doctype html" in message_lower
            or "cloudflare" in message_lower
            or "web server is down" in message_lower
        ):
            return (
                "Supabase is temporarily unavailable or still setting up the project. "
                "Please wait a few minutes, then try again."
            )

        if not message:
            return f"Supabase request failed with status {status_code}."

        return message[:500]

    def sign_up(self, email: str, password: str) -> dict[str, Any]:
        if not self.is_configured:
            return {"ok": False, "error": "Supabase config is missing."}

        url = f"{self.supabase_url}/auth/v1/signup"
        payload = {"email": email.strip(), "password": password}

        try:
            resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout_sec)
        except Exception as exc:
            return {"ok": False, "error": f"Network error: {exc}"}

        data = self._json_or_text(resp)
        if resp.status_code >= 400:
            message = self._friendly_error(resp, data, ("msg", "error_description", "error"))
            return {"ok": False, "error": message}

        return {
            "ok": True,
            "user": data.get("user") if isinstance(data, dict) else None,
            "access_token": data.get("access_token") if isinstance(data, dict) else None,
            "refresh_token": data.get("refresh_token") if isinstance(data, dict) else None,
            "raw": data,
        }

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        if not self.is_configured:
            return {"ok": False, "error": "Supabase config is missing."}

        url = f"{self.supabase_url}/auth/v1/token?grant_type=password"
        payload = {"email": email.strip(), "password": password}

        try:
            resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout_sec)
        except Exception as exc:
            return {"ok": False, "error": f"Network error: {exc}"}

        data = self._json_or_text(resp)
        if resp.status_code >= 400:
            message = self._friendly_error(resp, data, ("msg", "error_description", "error"))
            return {"ok": False, "error": message}

        return {
            "ok": True,
            "user": data.get("user") if isinstance(data, dict) else None,
            "access_token": data.get("access_token") if isinstance(data, dict) else None,
            "refresh_token": data.get("refresh_token") if isinstance(data, dict) else None,
            "raw": data,
        }

    def request_password_reset(self, email: str) -> dict[str, Any]:
        if not self.is_configured:
            return {"ok": False, "error": "Supabase config is missing."}

        url = f"{self.supabase_url}/auth/v1/recover"
        payload = {"email": email.strip()}

        try:
            resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout_sec)
        except Exception as exc:
            return {"ok": False, "error": f"Network error: {exc}"}

        data = self._json_or_text(resp)
        if resp.status_code >= 400:
            message = self._friendly_error(resp, data, ("msg", "error_description", "error"))
            return {"ok": False, "error": message}

        return {"ok": True, "raw": data}

    def refresh_session(self, refresh_token: str) -> dict[str, Any]:
        if not self.is_configured:
            return {"ok": False, "error": "Supabase config is missing."}

        clean_refresh_token = str(refresh_token or "").strip()
        if not clean_refresh_token:
            return {"ok": False, "error": "Missing refresh token."}

        url = f"{self.supabase_url}/auth/v1/token?grant_type=refresh_token"
        payload = {"refresh_token": clean_refresh_token}

        try:
            resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout_sec)
        except Exception as exc:
            return {"ok": False, "error": f"Network error: {exc}"}

        data = self._json_or_text(resp)
        if resp.status_code >= 400:
            message = self._friendly_error(resp, data, ("msg", "error_description", "error"))
            return {"ok": False, "error": message}

        return {
            "ok": True,
            "user": data.get("user") if isinstance(data, dict) else None,
            "access_token": data.get("access_token") if isinstance(data, dict) else None,
            "refresh_token": data.get("refresh_token") if isinstance(data, dict) else None,
            "raw": data,
        }

    def exchange_pkce_code(self, auth_code: str, code_verifier: str) -> dict[str, Any]:
        if not self.is_configured:
            return {"ok": False, "error": "Supabase config is missing."}

        clean_auth_code = str(auth_code or "").strip()
        clean_code_verifier = str(code_verifier or "").strip()
        if not clean_auth_code or not clean_code_verifier:
            return {"ok": False, "error": "Missing OAuth code verifier."}

        url = f"{self.supabase_url}/auth/v1/token?grant_type=pkce"
        payload = {"auth_code": clean_auth_code, "code_verifier": clean_code_verifier}

        try:
            resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout_sec)
        except Exception as exc:
            return {"ok": False, "error": f"Network error: {exc}"}

        data = self._json_or_text(resp)
        if resp.status_code >= 400:
            message = self._friendly_error(resp, data, ("msg", "error_description", "error"))
            return {"ok": False, "error": message}

        return {
            "ok": True,
            "user": data.get("user") if isinstance(data, dict) else None,
            "access_token": data.get("access_token") if isinstance(data, dict) else None,
            "refresh_token": data.get("refresh_token") if isinstance(data, dict) else None,
            "raw": data,
        }

    def get_user(self, access_token: str) -> dict[str, Any]:
        if not self.is_configured:
            return {"ok": False, "error": "Supabase config is missing."}

        url = f"{self.supabase_url}/auth/v1/user"
        try:
            resp = requests.get(url, headers=self._headers(access_token=access_token), timeout=self.timeout_sec)
        except Exception as exc:
            return {"ok": False, "error": f"Network error: {exc}"}

        data = self._json_or_text(resp)
        if resp.status_code >= 400:
            message = self._friendly_error(resp, data, ("msg", "error_description", "error"))
            return {"ok": False, "error": message}

        return {"ok": True, "user": data if isinstance(data, dict) else None}

    def upsert_user_data(self, user_id: str, access_token: str, data_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_configured:
            return {"ok": False, "error": "Supabase config is missing."}

        if not user_id:
            return {"ok": False, "error": "Missing user_id."}

        url = f"{self.supabase_url}/rest/v1/{self.table_name}"
        payload = [
            {
                "user_id": user_id,
                "data": data_payload,
                "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            }
        ]

        headers = self._headers(
            access_token=access_token,
            prefer="resolution=merge-duplicates,return=representation",
        )

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout_sec)
        except Exception as exc:
            return {"ok": False, "error": f"Network error: {exc}"}

        data = self._json_or_text(resp)
        if resp.status_code >= 400:
            message = self._friendly_error(resp, data, ("message", "hint", "error"))
            return {"ok": False, "error": message}

        return {"ok": True, "raw": data}

    def fetch_user_data(self, user_id: str, access_token: str) -> dict[str, Any]:
        if not self.is_configured:
            return {"ok": False, "error": "Supabase config is missing."}

        if not user_id:
            return {"ok": False, "error": "Missing user_id."}

        user_filter = quote(user_id, safe="")
        url = (
            f"{self.supabase_url}/rest/v1/{self.table_name}"
            f"?select=data,updated_at&user_id=eq.{user_filter}&limit=1"
        )

        try:
            resp = requests.get(url, headers=self._headers(access_token=access_token), timeout=self.timeout_sec)
        except Exception as exc:
            return {"ok": False, "error": f"Network error: {exc}"}

        data = self._json_or_text(resp)
        if resp.status_code >= 400:
            message = self._friendly_error(resp, data, ("message", "hint", "error"))
            return {"ok": False, "error": message}

        if isinstance(data, list) and data:
            row = data[0]
            return {
                "ok": True,
                "data": row.get("data") if isinstance(row, dict) else None,
                "updated_at": row.get("updated_at") if isinstance(row, dict) else None,
            }

        return {"ok": True, "data": None, "updated_at": None}

    def delete_user_data(self, user_id: str, access_token: str) -> dict[str, Any]:
        if not self.is_configured:
            return {"ok": False, "error": "Supabase config is missing."}

        if not user_id:
            return {"ok": False, "error": "Missing user_id."}

        user_filter = quote(user_id, safe="")
        url = f"{self.supabase_url}/rest/v1/{self.table_name}?user_id=eq.{user_filter}"

        try:
            resp = requests.delete(
                url,
                headers=self._headers(
                    access_token=access_token,
                    prefer="return=representation",
                ),
                timeout=self.timeout_sec,
            )
        except Exception as exc:
            return {"ok": False, "error": f"Network error: {exc}"}

        data = self._json_or_text(resp)
        if resp.status_code >= 400:
            message = self._friendly_error(resp, data, ("message", "hint", "error"))
            return {"ok": False, "error": message}

        return {"ok": True, "raw": data}


    def delete_current_user(self, access_token: str) -> dict[str, Any]:
        if not self.is_configured:
            return {"ok": False, "error": "Supabase config is missing."}

        if not access_token:
            return {"ok": False, "error": "Missing access token."}

        url = f"{self.supabase_url}/auth/v1/user"

        try:
            resp = requests.delete(
                url,
                headers=self._headers(access_token=access_token),
                timeout=self.timeout_sec,
            )
        except Exception as exc:
            return {"ok": False, "error": f"Network error: {exc}"}

        data = self._json_or_text(resp)
        if resp.status_code >= 400:
            message = self._friendly_error(resp, data, ("msg", "error_description", "error"))
            return {"ok": False, "error": message}

        return {"ok": True, "raw": data}

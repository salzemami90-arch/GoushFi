import json
import html
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import streamlit as st
import streamlit.components.v1 as components

from config_floosy import (
    CURRENCY_OPTIONS,
    PLAN_DEFINITIONS,
    _is_shared_hosted_url,
    _local_persistence_enabled,
    delete_local_persistent_copy,
    export_app_state_payload,
    get_plan_info,
    import_app_state_payload,
    reset_local_app_data,
    save_persistent_state,
    sync_browser_preferences_state,
)
from services.cloud_auth_cookie import clear_cloud_auth_cookie, remember_cloud_auth
from services.currency_localization import currency_option_label
from services.i18n import LANGUAGE_NAMES, LANGUAGES, make_t, is_rtl as _is_rtl
from services.cloud_sync_guard import (
    clear_cloud_sync_guard,
    cloud_sync_pause_reason,
    mark_cloud_sync_ready,
    payload_has_meaningful_data,
    payload_snapshot,
    pause_cloud_auto_sync,
    should_keep_local_data_before_auto_import,
)
from services.device_state_storage import browser_device_storage_available
from services.oauth_pkce import get_or_create_pkce_flow, render_pkce_cookie
from services.supabase_sync import SupabaseSyncClient


KUWAIT_TZ = timezone(timedelta(hours=3), name="Asia/Kuwait")
WEB_BUILD_MARKER = "2026-06-26-android-wrapper-1"


def _currency_option_label(value: str, lang_code: str) -> str:
    return currency_option_label(value, lang_code)


def _cloud_error_text(error, t) -> str:
    raw = str(error or "").strip()
    raw_lower = raw.lower()
    if (
        "temporarily unavailable or still setting up" in raw_lower
        or "web server is down" in raw_lower
        or "cloudflare" in raw_lower
        or "error code 521" in raw_lower
    ):
        return t(
            "السحابة غير متاحة مؤقتًا أو لا تزال قيد التجهيز. يرجى الانتظار دقائق ثم المحاولة مرة ثانية.",
            "Cloud is temporarily unavailable or still setting up. Wait a few minutes, then try again.",
        )
    return raw[:500]


from services.cloud_state_helpers import (
    clear_scoped_finance_state as _clear_scoped_finance_state,
    set_cloud_auth as _set_cloud_auth,
    set_scope_owner as _set_scope_owner,
)


def _refresh_cloud_auth_for_manual_action(client: SupabaseSyncClient) -> tuple[dict, str]:
    cloud_auth = st.session_state.get("cloud_auth", {})
    if not isinstance(cloud_auth, dict):
        return {}, "missing_cloud_auth"

    refresh_token = str(cloud_auth.get("refresh_token") or "").strip()
    if not refresh_token:
        return cloud_auth, ""

    refreshed = client.refresh_session(refresh_token)
    if not refreshed.get("ok"):
        st.session_state["_cloud_sync_last_error"] = "token_refresh_failed"
        pause_cloud_auto_sync(
            st.session_state,
            str(cloud_auth.get("user_id") or ""),
            reason="token_refresh_failed",
        )
        return cloud_auth, str(refreshed.get("error") or "token_refresh_failed")

    user_obj = refreshed.get("user") if isinstance(refreshed.get("user"), dict) else {}
    email = str(user_obj.get("email") or cloud_auth.get("email") or "")
    user_id = str(user_obj.get("id") or cloud_auth.get("user_id") or "")
    access_token = str(refreshed.get("access_token") or cloud_auth.get("access_token") or "")
    new_refresh_token = str(refreshed.get("refresh_token") or refresh_token)

    if not user_id or not access_token:
        return cloud_auth, "token_refresh_missing_auth"

    _set_cloud_auth(
        True,
        email=email,
        user_id=user_id,
        access_token=access_token,
        refresh_token=new_refresh_token,
    )
    if st.session_state.get("_cloud_remember_login") is not False:
        remember_cloud_auth(email, user_id, new_refresh_token)
    return st.session_state.get("cloud_auth", {}), ""


def _sign_out_cloud_session(client: SupabaseSyncClient | None = None) -> None:
    cloud_auth = st.session_state.get("cloud_auth", {})
    access_token = ""
    if isinstance(cloud_auth, dict):
        access_token = str(cloud_auth.get("access_token") or "").strip()

    if client is not None and access_token:
        try:
            client.sign_out(access_token)
        except Exception:
            # Sign-out cleanup must still complete locally if the network is down.
            pass

    _set_cloud_auth(False)
    _set_scope_owner("", "")
    st.session_state["_cloud_remember_login"] = False
    st.session_state["_cloud_cookie_restore_checked"] = True
    st.session_state["_cloud_browser_storage_clear_requested"] = True
    st.session_state["_cloud_auth_cookie_clear_pending"] = True
    st.session_state["_cloud_last_snapshot"] = ""
    st.session_state["_cloud_last_pull_user"] = ""
    st.session_state["_cloud_sync_last_error"] = ""
    clear_cloud_sync_guard(st.session_state)
    clear_cloud_auth_cookie(reload_after_write=False)


def _render_pending_cloud_auth_clear() -> None:
    if not st.session_state.pop("_cloud_auth_cookie_clear_pending", False):
        return
    clear_cloud_auth_cookie(reload_after_write=False)


def _safe_reset_local_app_data() -> tuple[bool, str]:
    try:
        reset_local_app_data()
    except Exception as exc:
        return False, str(exc or "local_reset_failed")
    return True, ""


def _safe_delete_local_persistent_copy() -> tuple[bool, str]:
    try:
        delete_local_persistent_copy()
    except Exception as exc:
        return False, str(exc or "local_delete_failed")
    return True, ""


def _fresh_start_app_data(client: SupabaseSyncClient | None = None) -> tuple[bool, str]:
    cloud_auth = st.session_state.get("cloud_auth", {})
    logged_in = bool(isinstance(cloud_auth, dict) and cloud_auth.get("logged_in") and cloud_auth.get("access_token"))

    if logged_in and client is not None and client.is_configured:
        try:
            cloud_auth, refresh_error = _refresh_cloud_auth_for_manual_action(client)
            if refresh_error:
                return False, str(refresh_error)

            delete_res = client.delete_user_data(
                str(cloud_auth.get("user_id") or ""),
                str(cloud_auth.get("access_token") or ""),
            )
            if not delete_res.get("ok"):
                return False, str(delete_res.get("error") or "cloud_delete_failed")
        except Exception as exc:
            return False, str(exc or "cloud_delete_failed")

    if logged_in:
        _sign_out_cloud_session(client)
    else:
        _set_cloud_auth(False)
        _set_scope_owner("", "")
        st.session_state["_cloud_remember_login"] = False
        st.session_state["_cloud_cookie_restore_checked"] = True
        st.session_state["_cloud_browser_storage_clear_requested"] = True
        st.session_state["_cloud_auth_cookie_clear_pending"] = True
        clear_cloud_auth_cookie(reload_after_write=False)

    reset_ok, reset_error = _safe_reset_local_app_data()
    if not reset_ok:
        return False, reset_error
    st.session_state["_device_browser_storage_clear_requested"] = True
    st.session_state["_cloud_browser_storage_clear_requested"] = True
    st.session_state["_cloud_cookie_restore_checked"] = True
    st.session_state["_cloud_remember_login"] = False
    clear_cloud_sync_guard(st.session_state)
    return True, ""


def _save_method_widget_state(device_save_available: bool) -> tuple[bool, bool]:
    settings = st.session_state.get("settings", {})
    if not isinstance(settings, dict):
        settings = {}
        st.session_state["settings"] = settings

    device_enabled = bool(settings.get("device_save_enabled", True)) and bool(device_save_available)
    cloud_enabled = bool(settings.get("cloud_sync_enabled", False))
    if device_enabled and cloud_enabled:
        device_enabled = False
        settings["device_save_enabled"] = False
        settings["cloud_sync_enabled"] = True
    if not device_save_available:
        device_enabled = False
        settings["device_save_enabled"] = False
    return device_enabled, cloud_enabled


def _on_device_save_method_changed() -> None:
    if bool(st.session_state.get("settings_device_save_enabled")):
        st.session_state["settings_cloud_sync_enabled"] = False


def _on_cloud_save_method_changed() -> None:
    if bool(st.session_state.get("settings_cloud_sync_enabled")):
        st.session_state["settings_device_save_enabled"] = False


def _cloud_sync_mode(settings: dict) -> str:
    mode = str(settings.get("cloud_sync_mode", "auto") or "auto").strip().lower()
    return mode if mode in {"auto", "manual"} else "auto"


def _sync_snapshot_from_state() -> None:
    try:
        st.session_state["_cloud_last_snapshot"] = json.dumps(
            export_app_state_payload(), ensure_ascii=False, sort_keys=True
        )
    except Exception:
        st.session_state["_cloud_last_snapshot"] = ""


def _mark_cloud_sync_now() -> None:
    settings = st.session_state.get("settings")
    if not isinstance(settings, dict):
        return
    settings["cloud_last_sync_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    st.session_state["settings"] = settings


def _cloud_remember_reload_after_write() -> bool:
    if bool(_local_persistence_enabled()):
        return False

    runtime_url = ""
    try:
        context = getattr(st, "context", None)
    except Exception:
        context = None

    if context is not None:
        try:
            runtime_url = str(getattr(context, "url", "") or "")
        except Exception:
            runtime_url = ""

        if not runtime_url:
            try:
                headers = getattr(context, "headers", {}) or {}
                if hasattr(headers, "get"):
                    host = str(headers.get("host") or headers.get("Host") or "").strip()
                elif isinstance(headers, dict):
                    host = str(headers.get("host") or headers.get("Host") or "").strip()
                else:
                    host = ""
                if host:
                    scheme = "http" if host.startswith(("localhost", "127.0.0.1", "0.0.0.0")) else "https"
                    runtime_url = f"{scheme}://{host}"
            except Exception:
                runtime_url = ""

    return _is_shared_hosted_url(runtime_url)


def _runtime_flag_value(*keys: str) -> bool | None:
    truthy = {"1", "true", "yes", "on", "enabled"}
    falsy = {"0", "false", "no", "off", "disabled"}

    for key in keys:
        raw_value = str(os.getenv(key, "") or "").strip().lower()
        if raw_value in truthy:
            return True
        if raw_value in falsy:
            return False

    secrets = getattr(st, "secrets", None)
    secret_sources = [secrets] if secrets is not None else []
    for section_name in ("supabase", "cloud", "auth"):
        try:
            section = secrets.get(section_name) if hasattr(secrets, "get") else None
        except Exception:
            section = None
        if section is not None:
            secret_sources.append(section)

    for source in secret_sources:
        for key in keys:
            value = None
            try:
                if hasattr(source, "get"):
                    value = source.get(key)
            except Exception:
                value = None
            if value is None:
                try:
                    value = source[key]
                except Exception:
                    value = None
            raw_value = str(value or "").strip().lower()
            if raw_value in truthy:
                return True
            if raw_value in falsy:
                return False

    return None


def _runtime_flag_enabled(*keys: str) -> bool:
    return _runtime_flag_value(*keys) is True


def _cloud_apple_sign_in_enabled() -> bool:
    if _runtime_flag_enabled("FLOOSY_DISABLE_APPLE_AUTH", "APPLE_SIGN_IN_DISABLED"):
        return False

    explicit_value = _runtime_flag_value(
        "FLOOSY_ENABLE_APPLE_AUTH",
        "SUPABASE_APPLE_AUTH_ENABLED",
        "APPLE_SIGN_IN_ENABLED",
    )
    return True if explicit_value is None else explicit_value


def _native_shell_oauth_uses_implicit_flow() -> bool:
    try:
        if str(st.query_params.get("f_shell", "") or "").strip() != "1":
            return False
        platform = str(st.query_params.get("f_platform", "") or "").strip().lower()
        return platform not in {"android", "android-webview", "goushfi-android"}
    except Exception:
        return False


def _current_runtime_url() -> str:
    try:
        context = getattr(st, "context", None)
    except Exception:
        context = None

    runtime_url = ""
    if context is not None:
        try:
            runtime_url = str(getattr(context, "url", "") or "")
        except Exception:
            runtime_url = ""

        if not runtime_url:
            try:
                headers = getattr(context, "headers", {}) or {}
                if hasattr(headers, "get"):
                    host = str(headers.get("host") or headers.get("Host") or "").strip()
                elif isinstance(headers, dict):
                    host = str(headers.get("host") or headers.get("Host") or "").strip()
                else:
                    host = ""
                if host:
                    scheme = "http" if host.startswith(("localhost", "127.0.0.1", "0.0.0.0")) else "https"
                    runtime_url = f"{scheme}://{host}"
            except Exception:
                runtime_url = ""

    return runtime_url


def _cloud_oauth_redirect_url(lang_code: str, pkce_state: str = "") -> str:
    runtime_url = _current_runtime_url().strip()
    if not runtime_url:
        return ""

    parts = urlsplit(runtime_url)
    if not parts.scheme or not parts.netloc:
        return ""

    query_items = dict(parse_qsl(parts.query, keep_blank_values=True))
    for stale_key in ("code", "state", "error", "error_code", "error_description", "cloud_oauth", "cloud_pkce_state"):
        query_items.pop(stale_key, None)
    query_items["page"] = "settings"
    query_items["cloud_oauth"] = "apple"
    if str(pkce_state or "").strip():
        query_items["cloud_pkce_state"] = str(pkce_state or "").strip()
    query_items["f_w"] = "1"
    query_items["f_lang"] = str(lang_code or "ar")
    if str(st.query_params.get("f_shell", "") or "").strip() == "1":
        query_items["f_shell"] = "1"
        platform = str(st.query_params.get("f_platform", "") or "").strip()
        if platform:
            query_items["f_platform"] = platform

    clean_path = parts.path or "/"
    return urlunsplit((parts.scheme, parts.netloc, clean_path, urlencode(query_items), ""))


def _render_same_tab_oauth_button(label: str, url: str) -> None:
    safe_label = html.escape(str(label or ""))
    safe_url = html.escape(str(url or ""), quote=True)
    button_style = html.escape(
        "display:flex;align-items:center;justify-content:center;width:100%;"
        "min-height:2.55rem;padding:0.45rem 0.85rem;margin:0.15rem 0 0.45rem 0;"
        "border:1px solid rgba(15,23,42,0.18);border-radius:0.5rem;background:#ffffff;"
        "color:#111827;font-weight:700;text-decoration:none;box-sizing:border-box;"
        "box-shadow:0 1px 2px rgba(15,23,42,0.05);",
        quote=True,
    )
    st.markdown(
        f'<a class="goushfi-oauth-apple-button" href="{safe_url}" target="_self" style="{button_style}">'
        f"{safe_label}</a>",
        unsafe_allow_html=True,
    )


def _render_native_apple_sign_in_button(label: str) -> None:
    _render_same_tab_oauth_button(label, "goushfi://native-apple-sign-in")


def _render_disabled_oauth_button(label: str) -> None:
    safe_label = html.escape(str(label or ""))
    button_style = html.escape(
        "display:flex;align-items:center;justify-content:center;width:100%;"
        "min-height:2.55rem;padding:0.45rem 0.85rem;"
        "margin:0.15rem 0 0.45rem 0;border:1px solid rgba(148,163,184,0.55);"
        "border-radius:0.5rem;background:rgba(248,250,252,0.86);color:rgba(75,88,124,0.62);"
        "font-weight:600;text-decoration:none;box-sizing:border-box;cursor:not-allowed;",
        quote=True,
    )
    st.markdown(
        f'<div class="goushfi-oauth-apple-button-disabled" aria-disabled="true" style="{button_style}">'
        f"{safe_label}</div>",
        unsafe_allow_html=True,
    )


def _render_same_tab_oauth_redirect(label: str, url: str) -> None:
    clean_url = str(url or "").strip()
    if not clean_url:
        return

    safe_label = html.escape(str(label or ""))
    safe_url = html.escape(clean_url, quote=True)
    url_json = json.dumps(clean_url)
    components.html(
        f"""
        <script>
        (function() {{
          const target = {url_json};
          const candidates = [window.parent, window.top, window];
          for (const candidate of candidates) {{
            try {{
              if (candidate && candidate.location) {{
                candidate.location.assign(target);
                return;
              }}
            }} catch (error) {{}}
          }}
        }})();
        </script>
        <meta http-equiv="refresh" content="0; url={safe_url}">
        """,
        height=0,
    )
    st.caption("Opening Apple sign-in...")
    _render_same_tab_oauth_button(label, clean_url)


def _create_initial_cloud_copy_after_sign_up(
    client: SupabaseSyncClient,
    user_id: str,
    access_token: str,
    email: str,
) -> tuple[bool, str]:
    payload = export_app_state_payload()
    push = client.upsert_user_data(user_id, access_token, payload)
    if not push.get("ok"):
        st.session_state["_cloud_sync_last_error"] = str(push.get("error") or "sync_push_failed")
        pause_cloud_auto_sync(st.session_state, user_id, reason="initial_cloud_copy_failed_after_sign_up")
        save_persistent_state()
        return False, str(push.get("error") or "sync_push_failed")

    _set_scope_owner(user_id, email)
    st.session_state["_cloud_sync_last_error"] = ""
    mark_cloud_sync_ready(st.session_state, user_id)
    _mark_cloud_sync_now()
    _sync_snapshot_from_state()
    save_persistent_state()
    return True, ""


def _format_cloud_sync_label(raw_value: str, empty_label: str) -> str:
    clean_value = str(raw_value or "").strip()
    if not clean_value:
        return empty_label
    try:
        parsed_sync = datetime.fromisoformat(clean_value.replace("Z", "+00:00"))
        if parsed_sync.tzinfo is None:
            parsed_sync = parsed_sync.replace(tzinfo=timezone.utc)
        return parsed_sync.astimezone(KUWAIT_TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return clean_value


def _build_backup_file() -> tuple[str, bytes]:
    backup_payload = export_app_state_payload()
    backup_now = datetime.now()
    backup_payload["_meta"] = {
        "saved_at": backup_now.isoformat(timespec="seconds"),
        "source": "goushfi_settings_backup",
        "version": 1,
    }
    timestamp_for_file = backup_now.strftime("%Y%m%d_%H%M%S")
    backup_name = f"goushfi_backup_{timestamp_for_file}.json"
    backup_bytes = json.dumps(backup_payload, ensure_ascii=False, indent=2).encode("utf-8")
    return backup_name, backup_bytes


def _get_app_scope() -> dict:
    scope = st.session_state.get("app_scope")
    if not isinstance(scope, dict):
        scope = {}
    scope.setdefault("owner_user_id", "")
    scope.setdefault("owner_email", "")
    st.session_state["app_scope"] = scope
    return scope





def _render_cloud_sync_pause_notice(t) -> None:
    reason = cloud_sync_pause_reason(st.session_state)

    if reason in {
        "local_cloud_conflict_after_sign_in",
        "local_cloud_conflict_after_cookie_restore",
        "local_cloud_conflict_after_auto_pull",
    }:
        if not payload_has_meaningful_data(export_app_state_payload()):
            clear_cloud_sync_guard(st.session_state)
            cloud_auth = st.session_state.get("cloud_auth", {})
            if isinstance(cloud_auth, dict) and cloud_auth.get("logged_in") and cloud_auth.get("user_id"):
                mark_cloud_sync_ready(st.session_state, str(cloud_auth.get("user_id") or ""))
            return

        st.warning(
            t(
                "وجدنا بيانات محلية مختلفة عن البيانات السحابية، لذلك أبقينا بيانات هذا الجهاز كما هي مؤقتًا. إذا أردت استبدالها بنسخة السحابة استخدم استعادة من السحابة، وإذا أردت رفع الحالية استخدم رفع للسحابة.",
                "We found local data that differs from the cloud copy, so this device kept its current local data for now. Use Restore from Cloud to replace it with the cloud copy, or Upload to Cloud to upload the current local data.",
            )
        )
    elif reason in {"cloud_empty_after_sign_in", "cloud_empty_after_cookie_restore"}:
        st.info(
            t(
                "لا توجد نسخة سحابية محفوظة لهذا الحساب حتى الآن. أضف أول عملية وسيُنشئ GoushFi النسخة السحابية تلقائيًا. ويمكنك أيضًا استخدام رفع للسحابة بعد إضافة بيانات.",
                "No cloud copy exists for this account yet. Add your first transaction and GoushFi will create the cloud copy automatically. You can also use Upload to Cloud after adding data.",
            )
        )
    elif reason in {"pull_failed_after_sign_in", "pull_failed_after_cookie_restore"}:
        st.warning(
            t(
                "تم تسجيل الدخول، لكن تعذر تحميل بيانات السحابة الآن. أبقينا بيانات هذا الجهاز كما هي، ويمكنك المحاولة لاحقًا من زر استعادة من السحابة.",
                "You are signed in, but cloud data could not be loaded right now. This device kept its current local data, and you can try again later with Restore from Cloud.",
            )
        )
    elif reason == "cloud_deleted_until_manual_save":
        st.info(
            t(
                "تم حذف النسخة السحابية، لذلك أوقفنا الرفع التلقائي مؤقتًا. إذا رغبت في إنشاء نسخة سحابية جديدة استخدم رفع للسحابة.",
                "The cloud copy was deleted, so auto upload is paused for now. If you want to create a new cloud copy, use Upload to Cloud.",
            )
        )
    elif reason == "initial_cloud_copy_failed_after_sign_up":
        st.warning(
            t(
                "تم إنشاء الحساب، لكن أول مزامنة سحابية لم تكتمل. بيانات هذا الجهاز بقيت كما هي ويمكنك المحاولة مرة ثانية من رفع للسحابة.",
                "Account created, but the first cloud sync did not complete. This device kept its data, and you can try again with Upload to Cloud.",
            )
        )
    elif reason == "token_refresh_failed":
        st.warning(
            t(
                "تعذر تجديد جلسة السحابة تلقائيًا. المزامنة التلقائية متوقفة مؤقتًا. يرجى تسجيل الخروج وإعادة تسجيل الدخول.",
                "Cloud session could not be refreshed automatically. Auto-sync is paused. Please sign out and sign in again.",
            )
        )

    sync_error = str(st.session_state.get("_cloud_sync_last_error") or "").strip()
    if sync_error and reason != "token_refresh_failed":
        st.caption(
            t(
                "آخر محاولة مزامنة تلقائية لم تنجح. بياناتك المحلية آمنة وستُحاول المزامنة مرة أخرى تلقائيًا.",
                "The last auto-sync attempt was not successful. Your local data is safe and sync will retry automatically.",
            )
        )


def _render_cloud_sql_setup(t):
    st.caption(t("SQL إعداد جدول البيانات مرة واحدة:", "One-time data table SQL:"))
    st.code(
        """
create table if not exists public.user_app_data (
  user_id uuid primary key references auth.users(id) on delete cascade,
  data jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

alter table public.user_app_data enable row level security;

drop policy if exists "Users can select own data" on public.user_app_data;

create policy "Users can select own data"
on public.user_app_data for select
using (auth.uid() = user_id);

drop policy if exists "Users can insert own data" on public.user_app_data;

create policy "Users can insert own data"
on public.user_app_data for insert
with check (auth.uid() = user_id);

drop policy if exists "Users can update own data" on public.user_app_data;

create policy "Users can update own data"
on public.user_app_data for update
using (auth.uid() = user_id)
with check (auth.uid() = user_id);

drop policy if exists "Users can delete own data" on public.user_app_data;

create policy "Users can delete own data"
on public.user_app_data for delete
using (auth.uid() = user_id);
        """.strip(),
        language="sql",
    )


def _render_settings_shell_css() -> None:
    st.markdown(
        """
        <style>
        section[data-testid="stMain"] .stMainBlockContainer.block-container,
        section[data-testid="stMain"] .block-container {
            max-width: min(1240px, 100%) !important;
            padding-top: 0 !important;
            padding-left: clamp(1rem, 2.2vw, 2rem) !important;
            padding-right: clamp(1rem, 2.2vw, 2rem) !important;
        }
        section[data-testid="stMain"] div[data-testid="stVerticalBlock"] {
            gap: 0.72rem;
        }
        section[data-testid="stMain"] h1 {
            margin-top: 0 !important;
            margin-bottom: 0.18rem !important;
            font-size: 1.86rem !important;
            line-height: 1.08 !important;
            letter-spacing: 0 !important;
            max-width: 100% !important;
            overflow-wrap: anywhere !important;
            padding-inline: 0.32rem !important;
            box-sizing: border-box !important;
        }
        section[data-testid="stMain"] h2,
        section[data-testid="stMain"] h3 {
            margin-top: 0.35rem !important;
            margin-bottom: 0.35rem !important;
            line-height: 1.18 !important;
            letter-spacing: 0 !important;
        }
        section[data-testid="stMain"] h3 {
            font-size: 1.38rem !important;
        }
        section[data-testid="stMain"] p,
        section[data-testid="stMain"] .stCaption,
        section[data-testid="stMain"] [data-testid="stCaptionContainer"] {
            line-height: 1.48;
        }
        .goushfi-settings-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
            margin: 8px 0 14px 0;
        }
        .goushfi-settings-card {
            border: 1px solid rgba(15,23,42,0.10);
            border-radius: 14px;
            background: rgba(255,255,255,0.82);
            padding: 13px 14px 11px 14px;
            min-height: 90px;
            box-shadow: 0 8px 20px rgba(15,23,42,0.045);
        }
        .goushfi-settings-card-title {
            font-size: 0.98rem;
            font-weight: 800;
            color: #0f172a;
            margin-bottom: 4px;
        }
        .goushfi-settings-card-caption {
            color: #64748b;
            font-size: 0.84rem;
            line-height: 1.45;
        }
        .goushfi-settings-section-kicker {
            color: #64748b;
            font-weight: 700;
            font-size: 0.78rem;
            margin-bottom: 0;
        }
        .goushfi-profile-card {
            display: flex;
            align-items: center;
            gap: 14px;
            border: 1px solid rgba(15,23,42,0.10);
            border-radius: 16px;
            background: linear-gradient(135deg, rgba(15,95,140,0.10), rgba(18,149,107,0.12));
            padding: 15px;
            margin: 6px 0 14px 0;
            box-shadow: 0 10px 22px rgba(15,23,42,0.055);
        }
        .goushfi-profile-avatar {
            width: 66px;
            height: 66px;
            border-radius: 999px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 66px;
            background: linear-gradient(135deg, #0f5f8c, #12956b);
            color: #fff;
            font-size: 1.55rem;
            font-weight: 800;
            box-shadow: 0 8px 16px rgba(15,95,140,0.20);
        }
        .goushfi-profile-name {
            font-size: 1.12rem;
            font-weight: 800;
            color: #0f172a;
            line-height: 1.24;
        }
        .goushfi-profile-meta {
            color: #475569;
            font-size: 0.86rem;
            margin-top: 3px;
            line-height: 1.45;
        }
        .goushfi-settings-summary-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 8px 12px;
            margin-top: 10px;
        }
        .goushfi-settings-summary-item {
            border-top: 1px solid rgba(15,23,42,0.08);
            padding-top: 7px;
            min-width: 0;
        }
        .goushfi-settings-summary-label {
            color: #64748b;
            font-size: 0.74rem;
            font-weight: 700;
            margin-bottom: 2px;
            line-height: 1.25;
        }
        .goushfi-settings-summary-value {
            color: #0f172a;
            font-size: 0.88rem;
            font-weight: 800;
            word-break: break-word;
            line-height: 1.32;
        }
        .goushfi-profile-fields {
            margin-top: 6px;
        }
        section[data-testid="stMain"] div[data-testid="stVerticalBlockBorderWrapper"] {
            border-color: rgba(15, 23, 42, 0.10) !important;
            border-radius: 15px !important;
            background: rgba(255, 255, 255, 0.64) !important;
            box-shadow: 0 8px 20px rgba(15, 23, 42, 0.035) !important;
        }
        section[data-testid="stMain"] div[data-testid="stExpander"] {
            border: 1px solid rgba(15, 95, 140, 0.10) !important;
            border-radius: 15px !important;
            background: rgba(255, 255, 255, 0.74) !important;
            box-shadow: 0 8px 20px rgba(15, 23, 42, 0.04) !important;
            margin: 0.15rem 0 0.45rem 0 !important;
        }
        section[data-testid="stMain"] div[data-testid="stExpander"] details summary {
            padding: 0.72rem 0.9rem !important;
            background: linear-gradient(90deg, rgba(15, 95, 140, 0.055), rgba(18, 149, 107, 0.055)) !important;
            border-bottom-color: rgba(15, 95, 140, 0.06) !important;
            font-size: 0.94rem !important;
            font-weight: 800 !important;
            line-height: 1.25 !important;
        }
        section[data-testid="stMain"] div[data-testid="stExpander"] details > div {
            padding: 0.42rem 0.5rem 0.35rem 0.5rem !important;
        }
        section[data-testid="stMain"] label[data-testid="stWidgetLabel"] p {
            font-size: 0.82rem !important;
            line-height: 1.18 !important;
            margin-bottom: 0.15rem !important;
        }
        section[data-testid="stMain"] .stButton button,
        section[data-testid="stMain"] .stDownloadButton button {
            min-height: 40px;
        }
        section[data-testid="stMain"] .stTextInput input,
        section[data-testid="stMain"] .stNumberInput input,
        section[data-testid="stMain"] .stDateInput input {
            min-height: 40px !important;
        }
        section[data-testid="stMain"] .stSelectbox [data-baseweb="select"] > div {
            min-height: 40px !important;
            display: flex !important;
            align-items: center !important;
        }
        @media (max-width: 1180px) {
            section[data-testid="stMain"] .stMainBlockContainer.block-container,
            section[data-testid="stMain"] .block-container {
                max-width: min(100%, 1120px) !important;
                padding-left: 0.85rem !important;
                padding-right: 0.85rem !important;
            }
            section[data-testid="stMain"] h1 {
                font-size: 1.42rem !important;
            }
        }
        @media (max-width: 760px) {
            .goushfi-settings-grid {
                grid-template-columns: 1fr;
            }
            .goushfi-profile-card {
                align-items: flex-start;
                flex-direction: column;
                gap: 10px;
                padding: 13px;
                margin-bottom: 10px;
            }
            .goushfi-settings-summary-grid {
                grid-template-columns: 1fr;
                gap: 7px;
            }
            section[data-testid="stMain"] .stMainBlockContainer.block-container,
            section[data-testid="stMain"] .block-container {
                padding-left: 0.75rem !important;
                padding-right: 0.75rem !important;
            }
            section[data-testid="stMain"] div[data-testid="stVerticalBlock"] {
                gap: 0.62rem;
            }
            section[data-testid="stMain"] h1 {
                font-size: 1.16rem !important;
                line-height: 1.16 !important;
            }
            section[data-testid="stMain"] h3 {
                font-size: 1.18rem !important;
            }
            section[data-testid="stMain"] div[data-testid="stExpander"] details summary {
                padding: 0.66rem 0.75rem !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _open_settings_section(section_key: str) -> None:
    st.session_state["settings_view"] = section_key
    st.rerun()


def render():
    settings = st.session_state.settings
    lang_code = LANGUAGES.get(settings.get("language", "العربية"), "ar")
    is_en = lang_code == "en"
    t = make_t()

    _render_pending_cloud_auth_clear()
    _render_settings_shell_css()
    st.title(t("إعدادات GoushFi", "GoushFi Settings"))
    st.caption(f"Web build: {WEB_BUILD_MARKER}")
    if st.session_state.pop("_cloud_signout_notice", False):
        st.success(t("تم تسجيل الخروج.", "Signed out."))
    if st.session_state.pop("_fresh_start_notice", False):
        st.success(
            t(
                "تم تصفير التطبيق. بيانات الجهاز والسحابة انمسحت، وتم تسجيل الخروج من السحابة.",
                "App reset complete. Device and cloud data were cleared, and cloud sign-in was removed.",
            )
        )
    oauth_notice = st.session_state.pop("_cloud_oauth_notice", None)
    if isinstance(oauth_notice, dict):
        notice_text = str(oauth_notice.get("en" if is_en else "ar") or oauth_notice.get("ar") or oauth_notice.get("en") or "")
        notice_type = str(oauth_notice.get("type") or "info")
        if notice_text:
            if notice_type == "success":
                st.success(notice_text)
            elif notice_type == "warning":
                st.warning(notice_text)
                oauth_detail = str(oauth_notice.get("detail") or st.session_state.get("_cloud_oauth_last_error_detail") or "").strip()
                if oauth_detail:
                    st.caption(oauth_detail)
                oauth_debug = st.session_state.get("_cloud_oauth_last_callback_debug")
                if isinstance(oauth_debug, dict) and _runtime_flag_enabled("FLOOSY_OAUTH_DEBUG", "OAUTH_DEBUG"):
                    st.caption(
                        "OAuth: "
                        f"code={'yes' if oauth_debug.get('code_present') else 'no'}, "
                        f"state={'yes' if oauth_debug.get('state_present') else 'no'}, "
                        f"redirect_state={'yes' if oauth_debug.get('redirect_state_present') else 'no'}, "
                        f"cookie={'yes' if oauth_debug.get('pkce_cookie_present') else 'no'}, "
                        f"session={'yes' if oauth_debug.get('pkce_session_present') else 'no'}, "
                        f"verifier={'yes' if oauth_debug.get('pkce_verifier_resolved') else 'no'}"
                    )
            else:
                st.info(notice_text)
    settings_view = str(st.session_state.get("settings_view", "home") or "home")

    section_labels = {
        "profile": t("الملف الشخصي", "Profile"),
        "data": t("البيانات والمزامنة", "Data & Sync"),
        "appearance": t("العرض", "Display"),
        "about": t("حول GoushFi", "About GoushFi"),
    }

    cloud_sync_enabled = bool(settings.get("cloud_sync_enabled", False))
    last_sync_raw = str(settings.get("cloud_last_sync_at", "") or "").strip()
    last_sync_label = _format_cloud_sync_label(
        last_sync_raw,
        t("لم تتم مزامنة بعد", "No sync yet"),
    )

    cloud_client = SupabaseSyncClient.from_runtime(getattr(st, "secrets", None))
    cloud_auth = st.session_state.get("cloud_auth", {})
    cloud_logged_in = bool(cloud_auth.get("logged_in")) and bool(cloud_auth.get("access_token"))
    device_save_available = bool(_local_persistence_enabled() or browser_device_storage_available())
    device_save_enabled, cloud_sync_enabled = _save_method_widget_state(device_save_available)
    st.session_state.settings = settings
    if not device_save_available:
        device_storage_line = t(
            "الجهاز: حفظ نسخة دائمة غير متاح في هذه البيئة",
            "Device: Permanent device save is not available in this environment",
        )
    else:
        device_storage_line = (
            t("الجهاز: حفظ نسخة على هذا الجهاز مفعّل", "Device: Saving a copy on this device is on")
            if device_save_enabled
            else t("الجهاز: حفظ نسخة على هذا الجهاز مطفأ", "Device: Saving a copy on this device is off")
        )

    # --- Save & sync status card ---
    if not cloud_sync_enabled:
        device_line = device_storage_line
        cloud_line = t("السحابة: غير مفعّلة", "Cloud: Not enabled")
        if device_save_enabled:
            warning_line = t(
                "بياناتك محفوظة على هذا الجهاز فقط. فعّل السحابة إذا تبي نسخة احتياطية آمنة.",
                "Your data is saved on this device only. Enable cloud if you want a safe backup copy.",
            )
        else:
            warning_line = t(
                "لا توجد نسخة دائمة حاليًا. فعّل حفظ الجهاز أو السحابة قبل إدخال بيانات مهمة.",
                "No permanent copy is active right now. Enable device save or cloud before entering important data.",
            )
        status_bg = "#fffbeb"
        status_border = "#f59e0b"
        status_text = "#92400e"
    elif not cloud_client.is_configured:
        device_line = device_storage_line
        cloud_line = t("السحابة: مفعّلة — غير متاحة في هذه البيئة", "Cloud: Enabled — not available in this environment")
        warning_line = t(
            "السحابة مفعلة، لكنها غير متاحة في هذه البيئة حاليًا.",
            "Cloud sync is enabled, but cloud is not available in this environment right now.",
        )
        status_bg = "#fff7ed"
        status_border = "#f97316"
        status_text = "#9a3412"
    elif cloud_logged_in:
        device_line = device_storage_line
        cloud_line = t(
            f"السحابة: متصلة ({cloud_auth.get('email', '')})",
            f"Cloud: Connected ({cloud_auth.get('email', '')})",
        )
        warning_line = (
            t("بياناتك بأمان — محفوظة على الجهاز والسحابة.", "Your data is safe — saved on device and cloud.")
            if device_save_enabled
            else t("بياناتك محفوظة في السحابة، ولا يتم حفظ نسخة دائمة على هذا الجهاز.", "Your data is saved in cloud, and no permanent copy is saved on this device.")
        )
        status_bg = "#ecfdf5"
        status_border = "#10b981"
        status_text = "#065f46"
    else:
        device_line = device_storage_line
        cloud_line = t("السحابة: مفعّلة — لم يتم تسجيل الدخول بعد", "Cloud: Enabled — not signed in yet")
        if device_save_enabled:
            warning_line = t(
                "سجّل دخول عشان تبدأ المزامنة. إلى ذلك الوقت، نسخة الجهاز مفعّلة.",
                "Sign in to start syncing. Until then, device save is on.",
            )
        else:
            warning_line = t(
                "سجّل دخول أو فعّل حفظ الجهاز قبل إدخال بيانات مهمة.",
                "Sign in or enable device save before entering important data.",
            )
        status_bg = "#eff6ff"
        status_border = "#2563eb"
        status_text = "#1d4ed8"

    if cloud_logged_in:
        cloud_status_summary = t("متصلة", "Connected")
    elif cloud_sync_enabled:
        cloud_status_summary = t("بانتظار تسجيل الدخول", "Waiting for sign-in")
    else:
        cloud_status_summary = t("غير مفعّلة", "Not enabled")

    if settings_view == "home":
        profile_name = str(st.session_state.get("settings_profile_name", settings.get("name", "")) or "").strip()
        display_name = profile_name or t("بدون اسم حتى الآن", "No name yet")
        avatar_initial = (display_name.strip()[:1] or "G").upper()
        currency_label = _currency_option_label(settings.get("default_currency", CURRENCY_OPTIONS[0]), lang_code)
        language_label = str(settings.get("language", "العربية") or "العربية")
        email_label = str(cloud_auth.get("email", "") or "-").strip() or "-"
        st.markdown(
            f"""
            <div class="goushfi-profile-card">
                <div class="goushfi-profile-avatar">{html.escape(avatar_initial)}</div>
                <div style="flex:1;min-width:0;">
                    <div class="goushfi-profile-name">{html.escape(display_name)}</div>
                    <div class="goushfi-profile-meta">{html.escape(t("ملخص الإعدادات", "Settings Summary"))}</div>
                    <div class="goushfi-settings-summary-grid">
                        <div class="goushfi-settings-summary-item">
                            <div class="goushfi-settings-summary-label">{html.escape(t("الاسم", "Name"))}</div>
                            <div class="goushfi-settings-summary-value">{html.escape(display_name)}</div>
                        </div>
                        <div class="goushfi-settings-summary-item">
                            <div class="goushfi-settings-summary-label">{html.escape(t("اللغة", "Language"))}</div>
                            <div class="goushfi-settings-summary-value">{html.escape(language_label)}</div>
                        </div>
                        <div class="goushfi-settings-summary-item">
                            <div class="goushfi-settings-summary-label">{html.escape(t("العملة", "Currency"))}</div>
                            <div class="goushfi-settings-summary-value">{html.escape(currency_label)}</div>
                        </div>
                        <div class="goushfi-settings-summary-item">
                            <div class="goushfi-settings-summary-label">{html.escape(t("الإيميل", "Email"))}</div>
                            <div class="goushfi-settings-summary-value">{html.escape(email_label)}</div>
                        </div>
                        <div class="goushfi-settings-summary-item">
                            <div class="goushfi-settings-summary-label">{html.escape(t("حالة السحابة", "Cloud Status"))}</div>
                            <div class="goushfi-settings-summary-value">{html.escape(cloud_status_summary)}</div>
                        </div>
                        <div class="goushfi-settings-summary-item">
                            <div class="goushfi-settings-summary-label">{html.escape(t("آخر مزامنة", "Last Sync"))}</div>
                            <div class="goushfi-settings-summary-value">{html.escape(last_sync_label)}</div>
                        </div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        c1, c2 = st.columns(2)
        with c1:
            if st.button(section_labels["profile"], use_container_width=True, key="settings_open_profile"):
                _open_settings_section("profile")
            st.caption(t("الاسم، اللغة، والعملة.", "Name, language, and currency."))
            if st.button(section_labels["appearance"], use_container_width=True, key="settings_open_appearance"):
                _open_settings_section("appearance")
            st.caption(t("إظهار بطاقات الصفحة الرئيسية وتفضيلات العرض.", "Home card visibility and display preferences."))
        with c2:
            if st.button(section_labels["data"], use_container_width=True, key="settings_open_data"):
                _open_settings_section("data")
            st.caption(t("حالة الحفظ، السحابة، بيانات الجهاز، والنسخ الاحتياطي.", "Save status, Cloud, device data, and backup."))
            if st.button(section_labels["about"], use_container_width=True, key="settings_open_about"):
                _open_settings_section("about")
            st.caption(t("الخطة الحالية، معلومات التطبيق، والسياسات.", "Current plan, app information, and policies."))
        return

    if settings_view not in section_labels:
        settings_view = "home"
        st.session_state["settings_view"] = "home"
        st.rerun()

    if st.button(t("رجوع إلى الإعدادات", "Back to Settings"), key="settings_back_btn"):
        _open_settings_section("home")

    st.markdown(
        f"""
        <div class="goushfi-settings-section-kicker">{t("الإعدادات", "Settings")}</div>
        <h3 style="margin-top:0;">{section_labels[settings_view]}</h3>
        """,
        unsafe_allow_html=True,
    )

    if settings_view == "data":
        with st.container(border=True):
            st.markdown(f"**{t('حالة الحفظ والمزامنة', 'Save & Sync Status')}**")
            st.write(device_line)
            st.write(cloud_line)
            if cloud_sync_enabled and cloud_logged_in:
                st.caption(f"{t('آخر مزامنة', 'Last Sync')}: {last_sync_label}")
            st.caption(warning_line)

    if settings_view == "profile":
        st.subheader(t("معلومات الملف", "Profile Details"))
        col1, col2 = st.columns(2)

        with col1:
            settings["name"] = st.text_input(
                t("اسم المستخدم / المنشأة", "User / Business Name"),
                value=settings.get("name", ""),
                key="settings_profile_name",
            )
            settings["default_currency"] = st.selectbox(
                t("العملة الافتراضية", "Default Currency"),
                CURRENCY_OPTIONS,
                index=CURRENCY_OPTIONS.index(settings.get("default_currency", CURRENCY_OPTIONS[0]))
                if settings.get("default_currency", CURRENCY_OPTIONS[0]) in CURRENCY_OPTIONS
                else 0,
                format_func=lambda opt: _currency_option_label(opt, lang_code),
            )

        with col2:
            current_language = settings.get("language", "العربية")
            lang_index = LANGUAGE_NAMES.index(current_language) if current_language in LANGUAGE_NAMES else 0
            selected_language = st.selectbox(
                t("اللغة", "Language"),
                LANGUAGE_NAMES,
                index=lang_index,
            )
            if selected_language != current_language:
                settings["language"] = selected_language
                settings["language_user_selected"] = True
                st.session_state.settings = settings
                sync_browser_preferences_state(
                    language=selected_language,
                    name=str(settings.get("name", "") or "").strip(),
                    welcome_done=True,
                )
                st.rerun()
            settings["language"] = selected_language

        st.session_state.settings = settings

    if settings_view == "appearance":
        with st.container(border=True):
            st.markdown(f"**{t('بطاقات الصفحة الرئيسية', 'Home Cards')}**")
            st.caption(t("البطاقات الظاهرة في الصفحة الرئيسية.", "Cards shown on the home page."))

            c1, c2, c3 = st.columns(3)
            with c1:
                settings["show_status_account"] = st.toggle(
                    t("الحساب", "Account"),
                    value=bool(settings.get("show_status_account", True)),
                    key="settings_show_status_account_toggle",
                )
            with c2:
                settings["show_status_saving"] = st.toggle(
                    t("التوفير", "Savings"),
                    value=bool(settings.get("show_status_saving", True)),
                    key="settings_show_status_saving_toggle",
                )
            with c3:
                settings["show_status_project"] = st.toggle(
                    t("المشاريع", "Projects"),
                    value=bool(settings.get("show_status_project", True)),
                    key="settings_show_status_project_toggle",
                )

        st.session_state.settings = settings

    if settings_view == "about":
        plan_info = get_plan_info()
        tier_key = str(plan_info.get("tier", "beta_free")).strip().lower()
        if tier_key not in PLAN_DEFINITIONS:
            tier_key = "beta_free"
        tier_meta = PLAN_DEFINITIONS[tier_key]
        tier_label = tier_meta["label_en"] if lang_code != "ar" else tier_meta["label_ar"]
        total_features = len(tier_meta.get("features", {}))
        enabled_count = sum(1 for enabled in tier_meta.get("features", {}).values() if enabled)

        with st.container(border=True):
            st.markdown("### GoushFi")
            st.caption("Flow · Control · Growth")
            st.write(
                t(
                    "مساحة شخصية لتنظيم الحسابات، التوفير، المستندات، والفواتير.",
                    "A personal space for accounts, savings, documents, and invoices.",
                )
            )

            version_col, plan_col, features_col = st.columns(3)
            with version_col:
                st.caption(t("الإصدار", "Version"))
                st.markdown("**Beta 1.0**")
                st.caption(f"Web build: {WEB_BUILD_MARKER}")
            with plan_col:
                st.caption(t("الخطة الحالية", "Current Plan"))
                st.markdown(f"**{tier_label}**")
            with features_col:
                st.caption(t("الميزات المفعلة", "Enabled Features"))
                st.markdown(f"**{enabled_count} / {total_features}**")

    if settings_view == "data":
        with st.container(border=True):
            st.markdown(f"**{t('طريقة الحفظ', 'Save Method')}**")
            st.caption(
                t(
                    "اختر مكان حفظ واحد لنسخة بياناتك. عند تفعيل خيار، يتم إيقاف الخيار الآخر تلقائيًا.",
                    "Choose one save location for your data copy. Turning one option on automatically turns the other off.",
                )
            )

            device_save_current, cloud_sync_current = _save_method_widget_state(device_save_available)
            if "settings_device_save_enabled" not in st.session_state:
                st.session_state["settings_device_save_enabled"] = device_save_current
            if "settings_cloud_sync_enabled" not in st.session_state:
                st.session_state["settings_cloud_sync_enabled"] = cloud_sync_current
            if not device_save_available:
                st.session_state["settings_device_save_enabled"] = False
            if (
                bool(st.session_state.get("settings_device_save_enabled"))
                and bool(st.session_state.get("settings_cloud_sync_enabled"))
            ):
                st.session_state["settings_device_save_enabled"] = False

            save_col, cloud_col = st.columns(2)
            with save_col:
                st.checkbox(
                    t("حفظ نسخة على هذا الجهاز", "Save a copy on this device"),
                    disabled=not device_save_available,
                    help=t(
                        "إذا أوقفته، لن يحتفظ التطبيق بنسخة دائمة على هذا الجهاز. استخدم السحابة لحفظ بياناتك.",
                        "When off, the app will not keep a permanent copy on this device. Use cloud to keep your data.",
                    ),
                    key="settings_device_save_enabled",
                    on_change=_on_device_save_method_changed,
                )
            with cloud_col:
                st.checkbox(
                    t("تفعيل المزامنة السحابية", "Enable Cloud Sync"),
                    help=t(
                        "اختياري. عند تسجيل الدخول، يمكن حفظ نسخة سحابية واستعادتها من جهاز آخر.",
                        "Optional. After sign-in, you can keep a cloud copy and restore it on another device.",
                    ),
                    key="settings_cloud_sync_enabled",
                    on_change=_on_cloud_save_method_changed,
                )

            device_save_choice = bool(st.session_state.get("settings_device_save_enabled")) and device_save_available
            cloud_sync_enabled = bool(st.session_state.get("settings_cloud_sync_enabled"))
            device_save_changed = device_save_choice != device_save_current
            cloud_sync_changed = cloud_sync_enabled != cloud_sync_current

            if device_save_changed or cloud_sync_changed:
                settings["device_save_enabled"] = device_save_choice
                settings["cloud_sync_enabled"] = cloud_sync_enabled
                st.session_state.settings = settings
                if not cloud_sync_enabled:
                    st.session_state["_cloud_last_snapshot"] = ""

                if device_save_choice:
                    if device_save_changed:
                        save_persistent_state()
                        st.success(t("تم تفعيل حفظ نسخة على هذا الجهاز.", "Device save is now on."))
                else:
                    if device_save_changed:
                        delete_ok, delete_error = _safe_delete_local_persistent_copy()
                        st.session_state["_device_browser_storage_clear_requested"] = True
                        if delete_ok:
                            st.warning(
                                t(
                                    "تم إيقاف حفظ الجهاز وحذف النسخة المحلية المحفوظة. بيانات الجلسة الحالية ما زالت مفتوحة الآن.",
                                    "Device save is now off and the saved local copy was deleted. Current session data is still open for now.",
                                )
                            )
                        else:
                            st.warning(
                                t(
                                    "تم إيقاف حفظ الجهاز، لكن تعذر حذف النسخة المحلية الآن. جربي مرة ثانية إذا استمر التنبيه.",
                                    "Device save is now off, but the local copy could not be deleted right now. Try again if the warning persists.",
                                )
                            )
                            st.caption(_cloud_error_text(delete_error, t))

                st.rerun()
            elif not device_save_available:
                st.caption(
                    t(
                        "حفظ نسخة دائمة على الجهاز غير مفعّل في هذه البيئة. استخدم السحابة للنسخة الدائمة.",
                        "Permanent device save is not enabled in this environment. Use cloud for the permanent copy.",
                    )
                )
            elif device_save_choice:
                st.caption(
                    t(
                        "بيانات الحسابات عادة صغيرة، وحفظ الجهاز يساعدك تكمل حتى قبل تسجيل الدخول.",
                        "Account data is usually small, and device save lets you continue before signing in.",
                    )
                )
            elif not cloud_logged_in:
                st.warning(
                    t(
                        "حفظ الجهاز مطفأ والسحابة غير متصلة. لا تدخل بيانات مهمة قبل تفعيل واحد منهم.",
                        "Device save is off and cloud is not connected. Do not enter important data before enabling one of them.",
                    )
                )

        cloud_auth = st.session_state.get("cloud_auth", {})

        with st.expander(t("حساب السحابة", "Cloud Account"), expanded=False):
            if not cloud_sync_enabled:
                st.caption(
                    t(
                        "السحابة غير مفعّلة. فعّلها إذا تبي نسخة محفوظة خارج هذا الجهاز.",
                        "Cloud is not enabled. Turn it on if you want a copy saved outside this device.",
                    )
                )
                if bool(cloud_auth.get("logged_in")) and bool(cloud_auth.get("access_token")):
                    if st.button(t("تسجيل خروج من السحابة", "Sign Out from Cloud"), key="cloud_signout_disabled_btn"):
                        _sign_out_cloud_session(SupabaseSyncClient.from_runtime(getattr(st, "secrets", None)))
                        st.session_state["_cloud_signout_notice"] = True
                        st.rerun()
            else:
                client = SupabaseSyncClient.from_runtime(getattr(st, "secrets", None))
                sync_mode_options = ["auto", "manual"]
                sync_mode_labels = {
                    "auto": t("تلقائية", "Automatic"),
                    "manual": t("يدوية", "Manual"),
                }
                current_sync_mode = _cloud_sync_mode(settings)
                selected_sync_mode = st.radio(
                    t("طريقة المزامنة", "Sync Mode"),
                    sync_mode_options,
                    index=sync_mode_options.index(current_sync_mode),
                    format_func=lambda mode: sync_mode_labels.get(mode, str(mode)),
                    horizontal=True,
                    key="cloud_sync_mode_choice",
                )
                if selected_sync_mode != current_sync_mode:
                    settings["cloud_sync_mode"] = selected_sync_mode
                    st.session_state.settings = settings
                    save_persistent_state()
                    st.rerun()

                if selected_sync_mode == "manual":
                    st.info(
                        t(
                            "المزامنة اليدوية مفعّلة. استخدم رفع للسحابة متى ما تبي تحفظ التغييرات أونلاين.",
                            "Manual sync is on. Use Upload to Cloud when you want to save changes online.",
                        )
                    )
                else:
                    st.caption(
                        t(
                            "GoushFi يرفع التغييرات تلقائيًا بعد تعديل البيانات.",
                            "GoushFi uploads changes automatically after you edit data.",
                        )
                    )

                if not client.is_configured:
                    st.info(
                        t(
                            "السحابة غير متاحة في هذه البيئة حاليًا.",
                            "Cloud is not available in this environment right now.",
                        )
                    )
                    st.divider()
                    st.caption(f"**{t('إعداد متقدم للمطورين', 'Advanced setup for developers')}**")
                    st.caption(
                        t(
                            "لبيئة النشر فقط: أضف مفاتيح ربط السحابة في secrets أو متغيرات البيئة.",
                            "Deployment only: add cloud connection keys in secrets or environment variables.",
                        )
                    )
                    st.caption("Required keys: SUPABASE_URL and SUPABASE_ANON_KEY")
                    _render_cloud_sql_setup(t)
                else:
                    logged_in = bool(cloud_auth.get("logged_in")) and bool(cloud_auth.get("access_token"))

                    if logged_in:
                        st.success(
                            t(
                                f"مسجل دخول: {cloud_auth.get('email', '')}",
                                f"Signed in: {cloud_auth.get('email', '')}",
                            )
                        )
                        _render_cloud_sync_pause_notice(t)

                        r1c1, r1c2 = st.columns(2)
                        with r1c1:
                            cloud_restore_confirm = st.checkbox(
                                t(
                                    "أفهم أن الاستعادة تستبدل بيانات الجهاز",
                                    "I understand restore replaces device data",
                                ),
                                key="cloud_restore_confirm",
                            )
                            if st.button(
                                t("استعادة من السحابة", "Restore from Cloud"),
                                use_container_width=True,
                                key="cloud_load_btn",
                                disabled=not cloud_restore_confirm,
                            ):
                                cloud_auth, refresh_error = _refresh_cloud_auth_for_manual_action(client)
                                if refresh_error:
                                    cloud_error = _cloud_error_text(refresh_error, t)
                                    st.error(t(f"تعذر تحديث الجلسة: {cloud_error}", f"Could not refresh session: {cloud_error}"))
                                    st.stop()
                                pull = client.fetch_user_data(cloud_auth.get("user_id", ""), cloud_auth.get("access_token", ""))
                                if not pull.get("ok"):
                                    cloud_error = _cloud_error_text(pull.get("error", ""), t)
                                    pause_cloud_auto_sync(
                                        st.session_state,
                                        cloud_auth.get("user_id", ""),
                                        reason="pull_failed_after_sign_in",
                                    )
                                    st.error(t(f"تعذر تحميل البيانات: {cloud_error}", f"Load failed: {cloud_error}"))
                                elif pull.get("data") is None:
                                    _set_scope_owner(cloud_auth.get("user_id", ""), cloud_auth.get("email", ""))
                                    pause_cloud_auto_sync(
                                        st.session_state,
                                        cloud_auth.get("user_id", ""),
                                        reason="cloud_empty_after_sign_in",
                                    )
                                    st.session_state["_cloud_last_snapshot"] = ""
                                    st.session_state["_cloud_last_pull_user"] = cloud_auth.get("user_id", "")
                                    save_persistent_state()
                                    st.info(
                                        t(
                                            "لا توجد بيانات محفوظة في السحابة حتى الآن. بيانات هذا الجهاز بقيت كما هي.",
                                            "No cloud data was found yet. This device kept its current local data.",
                                        )
                                    )
                                else:
                                    import_app_state_payload(pull.get("data"))
                                    _set_scope_owner(cloud_auth.get("user_id", ""), cloud_auth.get("email", ""))
                                    st.session_state["_cloud_last_pull_user"] = cloud_auth.get("user_id", "")
                                    st.session_state["_cloud_sync_last_error"] = ""
                                    mark_cloud_sync_ready(st.session_state, cloud_auth.get("user_id", ""))
                                    _mark_cloud_sync_now()
                                    _sync_snapshot_from_state()
                                    save_persistent_state()
                                    st.success(t("تم استعادة بياناتك من السحابة.", "Your data was restored from cloud."))
                                    st.rerun()

                        with r1c2:
                            if st.button(t("رفع للسحابة", "Upload to Cloud"), use_container_width=True, key="cloud_save_btn"):
                                payload = export_app_state_payload()
                                if not payload_has_meaningful_data(payload):
                                    st.warning(
                                        t(
                                            "لا توجد بيانات كافية لرفعها. تأكد من وجود معاملات أو التزامات قبل رفعها إلى السحابة.",
                                            "No meaningful data to upload. Make sure you have transactions or items before uploading to cloud.",
                                        )
                                    )
                                else:
                                    cloud_auth, refresh_error = _refresh_cloud_auth_for_manual_action(client)
                                    if refresh_error:
                                        cloud_error = _cloud_error_text(refresh_error, t)
                                        st.error(t(f"تعذر تحديث الجلسة: {cloud_error}", f"Could not refresh session: {cloud_error}"))
                                        st.stop()
                                    push = client.upsert_user_data(cloud_auth.get("user_id", ""), cloud_auth.get("access_token", ""), payload)
                                    if push.get("ok"):
                                        _set_scope_owner(cloud_auth.get("user_id", ""), cloud_auth.get("email", ""))
                                        st.session_state["_cloud_sync_last_error"] = ""
                                        mark_cloud_sync_ready(st.session_state, cloud_auth.get("user_id", ""))
                                        _mark_cloud_sync_now()
                                        _sync_snapshot_from_state()
                                        save_persistent_state()
                                        st.success(t("تم رفع بياناتك للسحابة.", "Your data was uploaded to cloud."))
                                    else:
                                        cloud_error = _cloud_error_text(push.get("error", ""), t)
                                        st.error(t(f"تعذر الرفع: {cloud_error}", f"Upload failed: {cloud_error}"))

                        r2c1, r2c2 = st.columns(2)
                        with r2c1:
                            if st.button(t("تسجيل خروج من السحابة", "Sign Out from Cloud"), use_container_width=True, key="cloud_signout_btn"):
                                _sign_out_cloud_session(client)
                                st.session_state["_cloud_signout_notice"] = True
                                st.rerun()

                        with r2c2:
                            delete_confirm = st.checkbox(
                                t("تأكيد حذف النسخة السحابية", "Confirm Delete Cloud Copy"),
                                key="cloud_delete_confirm",
                            )
                            if st.button(
                                t("حذف النسخة السحابية", "Delete Cloud Copy"),
                                use_container_width=True,
                                key="cloud_delete_btn",
                                disabled=not delete_confirm,
                            ):
                                cloud_auth, refresh_error = _refresh_cloud_auth_for_manual_action(client)
                                if refresh_error:
                                    cloud_error = _cloud_error_text(refresh_error, t)
                                    st.error(t(f"تعذر تحديث الجلسة: {cloud_error}", f"Could not refresh session: {cloud_error}"))
                                    st.stop()
                                delete_res = client.delete_user_data(cloud_auth.get("user_id", ""), cloud_auth.get("access_token", ""))
                                if delete_res.get("ok"):
                                    st.session_state["_cloud_last_pull_user"] = cloud_auth.get("user_id", "")
                                    st.session_state["_cloud_last_snapshot"] = ""
                                    pause_cloud_auto_sync(
                                        st.session_state,
                                        cloud_auth.get("user_id", ""),
                                        reason="cloud_deleted_until_manual_save",
                                    )
                                    _sync_snapshot_from_state()
                                    st.success(t("تم حذف النسخة السحابية بالكامل.", "Cloud copy was deleted."))
                                    st.info(
                                        t(
                                            "لن يتم رفع البيانات تلقائيًا بعد الحذف. إذا رغبت في إنشاء نسخة سحابية جديدة، استخدم زر رفع للسحابة.",
                                            "Auto upload is paused after deletion. If you want to create a new cloud copy, use Upload to Cloud.",
                                        )
                                    )
                                    st.rerun()
                                else:
                                    cloud_error = _cloud_error_text(delete_res.get("error", ""), t)
                                    st.error(t(f"تعذر حذف البيانات: {cloud_error}", f"Delete failed: {cloud_error}"))

                        st.caption(
                            t(
                                "الحذف يشمل النسخة السحابية فقط. بيانات هذا الجهاز لن تتأثر.",
                                "This deletes the cloud copy only. Data on this device is not affected.",
                            )
                        )

                        st.divider()
                        st.caption(f"⚠️ **{t('منطقة حساسة', 'Danger Zone')}**")
                        if not client.can_delete_auth_users:
                            st.info(
                                t(
                                    "حذف حساب تسجيل الدخول نهائيًا يحتاج إعداد مفتاح آمن على السيرفر. لحذف بيانات التطبيق فقط، استخدم حذف النسخة السحابية ومسح بيانات الجهاز.",
                                    "Permanent sign-in account deletion requires a secure server key. To clear app data only, use Delete Cloud Copy and Clear This Device Data.",
                                )
                            )
                        else:
                            st.warning(
                                t(
                                    "حذف الحساب بالكامل نهائي ويشمل حساب السحابة وبياناته.",
                                    "Permanent account deletion removes your cloud account and cloud data.",
                                )
                            )

                            delete_account_confirm = st.checkbox(
                                t("أفهم أن حذف الحساب نهائي", "I understand account deletion is permanent"),
                                key="cloud_delete_account_confirm",
                            )

                            if st.button(
                                t("حذف الحساب بالكامل", "Delete Account Permanently"),
                                use_container_width=True,
                                key="cloud_delete_account_btn",
                                disabled=not delete_account_confirm,
                            ):
                                cloud_auth, refresh_error = _refresh_cloud_auth_for_manual_action(client)
                                if refresh_error:
                                    cloud_error = _cloud_error_text(refresh_error, t)
                                    st.error(t(f"تعذر تحديث الجلسة: {cloud_error}", f"Could not refresh session: {cloud_error}"))
                                    st.stop()
                                data_delete_res = client.delete_user_data(
                                    cloud_auth.get("user_id", ""),
                                    cloud_auth.get("access_token", ""),
                                )
                                account_delete_res = client.delete_current_user(cloud_auth.get("user_id", ""))

                                if account_delete_res.get("ok"):
                                    _sign_out_cloud_session(client)
                                    st.success(
                                        t(
                                            "تم حذف الحساب السحابي بالكامل.",
                                            "Cloud account deleted permanently.",
                                        )
                                    )
                                    st.stop()

                                account_error = str(account_delete_res.get("error", ""))
                                data_error = str(data_delete_res.get("error", ""))
                                account_error_display = _cloud_error_text(account_error, t)
                                data_error_display = _cloud_error_text(data_error, t)
                                if data_delete_res.get("ok"):
                                    st.warning(
                                        t(
                                            f"تم حذف البيانات السحابية لكن حذف الحساب فشل: {account_error_display}",
                                            f"Cloud data deleted, but account deletion failed: {account_error_display}",
                                        )
                                    )
                                else:
                                    st.error(
                                        t(
                                            f"تعذر حذف الحساب: {account_error_display} | وتعذر حذف البيانات: {data_error_display}",
                                            f"Account deletion failed: {account_error_display} | Data deletion failed: {data_error_display}",
                                        )
                                    )

                    else:
                        mode = st.selectbox(
                            t("حساب السحابة", "Cloud Account"),
                            [
                                t("تسجيل دخول", "Sign In"),
                                t("إنشاء حساب", "Sign Up"),
                                t("نسيت كلمة المرور", "Forgot Password"),
                            ],
                            key="cloud_auth_mode",
                        )
                        is_sign_up_mode = mode == t("إنشاء حساب", "Sign Up")
                        is_reset_mode = mode == t("نسيت كلمة المرور", "Forgot Password")

                        if not is_reset_mode:
                            native_shell_oauth = _native_shell_oauth_uses_implicit_flow()
                            apple_enabled = _cloud_apple_sign_in_enabled()
                            apple_label = t("متابعة باستخدام Apple", "Continue with Apple")
                            if native_shell_oauth:
                                if apple_enabled:
                                    _render_native_apple_sign_in_button(apple_label)
                                    st.caption(
                                        t(
                                            "يفتح تسجيل Apple الأصلي على iPhone باستخدام Face ID أو رمز الجهاز.",
                                            "Opens native iPhone Apple sign-in with Face ID or device passcode.",
                                        )
                                    )
                                else:
                                    _render_disabled_oauth_button(apple_label)
                                    st.caption(
                                        t(
                                            "تسجيل الدخول بأبل متوقف من إعدادات البيئة. تسجيل الدخول بالإيميل يعمل مؤقتًا.",
                                            "Apple sign-in is disabled in the environment settings. Email sign-in works for now.",
                                        )
                                    )
                            else:
                                apple_pkce_flow = {}
                                apple_pkce_flow = get_or_create_pkce_flow(st.session_state)
                                render_pkce_cookie(apple_pkce_flow)
                                apple_redirect_url = _cloud_oauth_redirect_url(lang_code, str(apple_pkce_flow.get("state") or ""))
                                apple_auth_url = client.build_oauth_authorize_url(
                                    "apple",
                                    apple_redirect_url,
                                    scopes="name email",
                                    code_challenge=str(apple_pkce_flow.get("code_challenge") or ""),
                                    code_challenge_method=str(apple_pkce_flow.get("code_challenge_method") or ""),
                                    state=str(apple_pkce_flow.get("state") or ""),
                                )
                                if apple_enabled and apple_auth_url:
                                    _render_same_tab_oauth_button(apple_label, apple_auth_url)
                                else:
                                    _render_disabled_oauth_button(apple_label)
                                    missing_setup_reason = (
                                        t(
                                            "تسجيل الدخول بأبل متوقف من إعدادات البيئة.",
                                            "Apple sign-in is disabled in the environment settings.",
                                        )
                                        if not apple_enabled
                                        else t(
                                            "تعذر تجهيز رابط Apple. تأكد من إعدادات Supabase قبل التجربة.",
                                            "Could not prepare the Apple link. Check Supabase settings before testing.",
                                        )
                                    )
                                    st.caption(
                                        t(
                                            f"{missing_setup_reason} تسجيل الدخول بالإيميل يعمل مؤقتًا.",
                                            f"{missing_setup_reason} Email sign-in works for now.",
                                        )
                                    )
                            st.caption(t("أو", "Or"))

                        email = st.text_input(t("الإيميل", "Email"), type="default", key="cloud_auth_email")
                        password = ""
                        confirm_password = ""
                        if not is_reset_mode:
                            password = st.text_input(t("كلمة المرور", "Password"), type="password", key="cloud_auth_password")
                        if is_sign_up_mode:
                            confirm_password = st.text_input(t("تأكيد كلمة المرور", "Confirm Password"), type="password", key="cloud_auth_confirm_password")
                        remember_login = False
                        if not is_reset_mode:
                            remember_login = st.checkbox(
                                t("تذكر تسجيل الدخول على هذا الجهاز", "Remember sign-in on this device"),
                                value=True,
                                key="cloud_remember_login",
                            )
                            st.caption(
                                t(
                                    "عند التفعيل، يبقى تسجيل الدخول محفوظًا على هذا المتصفح. استخدم تسجيل الخروج لإزالته.",
                                    "When enabled, sign-in stays saved on this browser. Use Sign Out to remove it.",
                                )
                            )
                        submit_label = t("إرسال رابط الاستعادة", "Send Reset Link") if is_reset_mode else t("متابعة", "Continue")
                        submit = st.button(submit_label, use_container_width=True, key="cloud_auth_submit")

                        if submit:
                            clean_email = email.strip().lower()
                            if not clean_email:
                                st.warning(t("يرجى إدخال البريد الإلكتروني.", "Please enter your email."))
                            elif is_reset_mode:
                                reset_res = client.request_password_reset(clean_email)
                                if reset_res.get("ok"):
                                    st.success(
                                        t(
                                            "إذا كان الإيميل مسجلًا، سيتم إرسال رابط استعادة كلمة المرور. يرجى مراجعة البريد والـ Spam.",
                                            "If the email is registered, a password reset link will be sent. Check your inbox and spam folder.",
                                        )
                                    )
                                    st.caption(
                                        t(
                                            "إذا لم يصل الإيميل، حاول مرة أخرى أو تواصل مع الدعم.",
                                            "If the email does not arrive, try again or contact support.",
                                        )
                                    )
                                else:
                                    cloud_error = _cloud_error_text(reset_res.get("error", ""), t)
                                    st.error(t(f"تعذر إرسال رابط الاستعادة: {cloud_error}", f"Could not send reset link: {cloud_error}"))
                            elif not password:
                                st.warning(t("يرجى إدخال البريد الإلكتروني وكلمة المرور.", "Please enter email and password."))
                            elif is_sign_up_mode and password != confirm_password:
                                st.warning(t("كلمتا المرور غير متطابقتين.", "Passwords do not match."))
                            elif is_sign_up_mode and len(password) < 6:
                                st.warning(t("كلمة المرور يجب أن تكون 6 أحرف على الأقل.", "Password must be at least 6 characters."))
                            else:
                                if mode == t("إنشاء حساب", "Sign Up"):
                                    auth_res = client.sign_up(clean_email, password)
                                else:
                                    auth_res = client.sign_in(clean_email, password)

                                if not auth_res.get("ok"):
                                    cloud_error = _cloud_error_text(auth_res.get("error", ""), t)
                                    st.error(t(f"تعذر تنفيذ الطلب: {cloud_error}", f"Request failed: {cloud_error}"))
                                else:
                                    access_token = str(auth_res.get("access_token") or "")
                                    refresh_token = str(auth_res.get("refresh_token") or "")
                                    user_obj = auth_res.get("user") if isinstance(auth_res.get("user"), dict) else {}

                                    if not access_token:
                                        st.info(
                                            t(
                                                "تم إنشاء الحساب. إذا كان التحقق عبر البريد الإلكتروني مفعّلًا، يرجى مراجعة البريد والـ Spam ثم تسجيل الدخول.",
                                                "Account created. If email confirmation is enabled, check your inbox and spam folder, then sign in.",
                                            )
                                        )
                                    else:
                                        user_id = str(user_obj.get("id") or "")
                                        if not user_id:
                                            user_res = client.get_user(access_token)
                                            if user_res.get("ok") and isinstance(user_res.get("user"), dict):
                                                user_id = str(user_res["user"].get("id") or "")

                                        if not user_id:
                                            st.error(t("تعذر التحقق من الحساب. يرجى المحاولة مرة أخرى.", "Could not verify your account. Please try again."))
                                        else:
                                            scope = _get_app_scope()
                                            previous_owner = str(scope.get("owner_user_id") or "")
                                            if previous_owner and previous_owner != user_id:
                                                _clear_scoped_finance_state()
                                                st.info(
                                                    t(
                                                        "تم تبديل الحساب. تم فتح مساحة بيانات محلية مستقلة لهذا المستخدم.",
                                                        "Account switched. A separate local data scope has been opened for this user.",
                                                    )
                                                )

                                            _set_cloud_auth(
                                                True,
                                                email=clean_email,
                                                user_id=user_id,
                                                access_token=access_token,
                                                refresh_token=refresh_token,
                                            )
                                            st.session_state["_cloud_remember_login"] = bool(remember_login)
                                            if not remember_login:
                                                clear_cloud_auth_cookie()
                                                st.session_state["_cloud_browser_storage_clear_requested"] = True
                                            _set_scope_owner(user_id, clean_email)

                                            local_payload = export_app_state_payload()
                                            pull = client.fetch_user_data(user_id, access_token)
                                            remote_payload = pull.get("data") if isinstance(pull.get("data"), dict) else None
                                            post_login_message = ""
                                            post_login_caption = ""
                                            post_login_message_type = "info"
                                            if pull.get("ok") and remote_payload is not None:
                                                if should_keep_local_data_before_auto_import(local_payload, remote_payload):
                                                    pause_cloud_auto_sync(
                                                        st.session_state,
                                                        user_id,
                                                        reason="local_cloud_conflict_after_sign_in",
                                                    )
                                                    st.session_state["_cloud_last_snapshot"] = payload_snapshot(remote_payload)
                                                    st.session_state["_cloud_last_pull_user"] = user_id
                                                    save_persistent_state()
                                                    post_login_message_type = "warning"
                                                    post_login_message = t(
                                                        "تم تسجيل الدخول، لكن وجدنا بيانات محلية مختلفة عن نسخة السحابة. أبقينا بيانات هذا الجهاز كما هي. استخدم استعادة من السحابة إذا أردت استبدالها بنسخة السحابة، أو رفع للسحابة إذا أردت رفع الحالية.",
                                                        "Signed in, but we found local data that differs from the cloud copy. This device kept its current local data. Use Restore from Cloud to replace it with the cloud copy, or Upload to Cloud to upload the current local data.",
                                                    )
                                                else:
                                                    import_app_state_payload(remote_payload)
                                                    _set_scope_owner(user_id, clean_email)
                                                    st.session_state["_cloud_last_pull_user"] = user_id
                                                    mark_cloud_sync_ready(st.session_state, user_id)
                                                    _mark_cloud_sync_now()
                                                    _sync_snapshot_from_state()
                                                    save_persistent_state()
                                                    post_login_message_type = "success"
                                                    post_login_message = t("تم تسجيل الدخول وتحميل بياناتك.", "Signed in and data loaded.")

                                            elif pull.get("ok") and pull.get("data") is None:
                                                _set_scope_owner(user_id, clean_email)
                                                st.session_state["_cloud_last_pull_user"] = user_id
                                                st.session_state["_cloud_last_snapshot"] = ""
                                                if is_sign_up_mode:
                                                    initial_sync_ok, initial_sync_error = _create_initial_cloud_copy_after_sign_up(
                                                        client,
                                                        user_id,
                                                        access_token,
                                                        clean_email,
                                                    )
                                                    if initial_sync_ok:
                                                        post_login_message_type = "success"
                                                        post_login_message = t(
                                                            "تم إنشاء الحساب وتفعيل المزامنة السحابية.",
                                                            "Account created and Cloud Sync is ready.",
                                                        )
                                                    else:
                                                        cloud_error = _cloud_error_text(initial_sync_error, t)
                                                        post_login_message_type = "warning"
                                                        post_login_message = t(
                                                            f"تم إنشاء الحساب، لكن أول مزامنة سحابية لم تكتمل: {cloud_error}",
                                                            f"Account created, but the first cloud sync did not complete: {cloud_error}",
                                                        )
                                                else:
                                                    pause_cloud_auto_sync(
                                                        st.session_state,
                                                        user_id,
                                                        reason="cloud_empty_after_sign_in",
                                                    )
                                                    save_persistent_state()
                                                    post_login_message_type = "info"
                                                    post_login_message = t(
                                                        "تم تسجيل الدخول. لا توجد بيانات محفوظة في السحابة حاليًا.",
                                                        "Signed in. No cloud data exists yet.",
                                                    )
                                                    post_login_caption = t(
                                                        "أضف أول عملية وسيُنشئ GoushFi النسخة السحابية تلقائيًا. ويمكنك أيضًا استخدام رفع للسحابة بعد إضافة بيانات.",
                                                        "Add your first transaction and GoushFi will create the cloud copy automatically. You can also use Upload to Cloud after adding data.",
                                                    )
                                            else:
                                                pause_cloud_auto_sync(
                                                    st.session_state,
                                                    user_id,
                                                    reason="pull_failed_after_sign_in",
                                                )
                                                save_persistent_state()
                                                post_login_message_type = "warning"
                                                post_login_message = t(
                                                    "تم تسجيل الدخول، لكن تعذر تحميل البيانات السحابية الآن. أبقينا بيانات هذا الجهاز كما هي.",
                                                    "Signed in, but failed to load cloud data now. This device kept its current local data.",
                                                )

                                            if post_login_message:
                                                if post_login_message_type == "success":
                                                    st.success(post_login_message)
                                                elif post_login_message_type == "warning":
                                                    st.warning(post_login_message)
                                                else:
                                                    st.info(post_login_message)
                                            if post_login_caption:
                                                st.caption(post_login_caption)

                                            if remember_login:
                                                st.session_state["_cloud_cookie_restore_checked"] = False
                                                reload_after_write = _cloud_remember_reload_after_write()
                                                remember_cloud_auth(
                                                    clean_email,
                                                    user_id,
                                                    refresh_token,
                                                    reload_after_write=reload_after_write,
                                                )
                                                if reload_after_write:
                                                    st.stop()

                                            st.rerun()

        with st.expander(t("تصفير التطبيق", "Start Fresh"), expanded=False):
            st.warning(
                t(
                    "يمسح بيانات التطبيق من هذا الجهاز ومن السحابة، ثم يسجل خروج من السحابة. لا يحذف حساب Apple أو الإيميل نفسه.",
                    "Clears this app's data from this device and the cloud, then signs out of cloud. It does not delete the Apple or email account itself.",
                )
            )
            fresh_start_confirm = st.checkbox(
                t(
                    "أفهم أن هذا يمسح بيانات التطبيق من الجهاز والسحابة",
                    "I understand this clears app data from this device and cloud",
                ),
                key="fresh_start_confirm",
            )
            if st.button(
                t("تصفير التطبيق بالكامل", "Start Fresh Completely"),
                use_container_width=True,
                key="fresh_start_btn",
                disabled=not fresh_start_confirm,
            ):
                fresh_client = SupabaseSyncClient.from_runtime(getattr(st, "secrets", None))
                ok, fresh_error = _fresh_start_app_data(fresh_client)
                if ok:
                    st.session_state["_fresh_start_notice"] = True
                    st.rerun()
                st.error(
                    t(
                        "تعذر تصفير التطبيق. لم يتم مسح بيانات الجهاز لأن حذف السحابة لم يكتمل.",
                        "Could not start fresh. Device data was not cleared because cloud deletion did not finish.",
                    )
                )
                if fresh_error:
                    st.caption(_cloud_error_text(fresh_error, t))

        # --- Device Data section (below cloud, always visible) ---
        with st.expander(t("بيانات هذا الجهاز", "This Device Data"), expanded=False):
            st.caption(
                t(
                    "يمسح كل البيانات من هذا المتصفح فقط. النسخة السحابية (إن وجدت) لن تتأثر.",
                    "Clears all data from this browser only. Cloud copy (if any) will not be affected.",
                )
            )

            local_delete_confirm = st.checkbox(
                t("تأكيد مسح بيانات الجهاز", "Confirm Clear Device Data"),
                key="local_delete_confirm",
            )

            if st.button(
                t("مسح بيانات هذا الجهاز", "Clear This Device Data"),
                use_container_width=True,
                key="local_delete_btn",
                disabled=not local_delete_confirm,
            ):
                reset_local_app_data()
                st.success(t("تم مسح بيانات هذا الجهاز.", "This device data was cleared."))
                st.info(
                    t(
                        "لاستعادة البيانات، استخدم النسخة السحابية أو ملف النسخة الاحتياطية (JSON).",
                        "To restore data, use the cloud copy or a backup file (JSON).",
                    )
                )
                st.rerun()

        # --- Backup section ---
        with st.expander(t("نسخ احتياطي (JSON)", "Backup (JSON)"), expanded=False):
            st.caption(
                t(
                    "ملف JSON يحتوي كل بياناتك. يمكنك استخدامه لنقل البيانات لجهاز آخر أو كنسخة أمان.",
                    "A JSON file containing all your data. Use it to transfer data to another device or as a safety backup.",
                )
            )
            backup_name, backup_bytes = _build_backup_file()

            st.download_button(
                label=t("تصدير بياناتي", "Export My Data"),
                data=backup_bytes,
                file_name=backup_name,
                mime="application/json",
                use_container_width=True,
                key="settings_backup_download_btn",
            )
            st.caption(
                t(
                    "مكان الحفظ يعتمد على إعدادات المتصفح (غالبًا مجلد Downloads).",
                    "Save location depends on your browser settings (usually Downloads).",
                )
            )

            restore_file = st.file_uploader(
                t("اختيار ملف بيانات JSON", "Choose JSON Data File"),
                type=["json"],
                key="settings_restore_file",
                help=t(
                    "تنبيه: الاسترجاع يستبدل البيانات الحالية على هذا الجهاز.",
                    "Warning: restore replaces current data on this device.",
                ),
            )

            restore_confirm = st.checkbox(
                t("أفهم أن الاسترجاع يستبدل البيانات الحالية", "I understand restore replaces current data"),
                key="settings_restore_confirm",
            )

            if st.button(
                t("استرجاع النسخة الآن", "Restore Backup Now"),
                use_container_width=True,
                key="settings_restore_btn",
                disabled=(restore_file is None or not restore_confirm),
            ):
                raw_bytes = restore_file.getvalue() if restore_file is not None else b""
                try:
                    loaded = json.loads(raw_bytes.decode("utf-8-sig"))
                except Exception:
                    st.error(t("الملف غير صالح JSON.", "Invalid JSON file."))
                else:
                    if not isinstance(loaded, dict):
                        st.error(t("محتوى النسخة غير صالح.", "Backup content is invalid."))
                    else:
                        import_app_state_payload(loaded)
                        save_persistent_state()
                        _sync_snapshot_from_state()
                        st.success(t("تم استرجاع النسخة بنجاح.", "Backup restored successfully."))
                        st.rerun()

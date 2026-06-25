import json
import traceback
from datetime import datetime, timezone

import streamlit as st

from config_floosy import (
    ensure_month_keys,
    export_app_state_payload,
    get_builtin_logo_b64,
    get_month_selection,
    import_app_state_payload,
    init_session_state,
    _is_native_shell_runtime,
    _local_persistence_enabled,
    save_persistent_state,
    clear_regular_web_page_query_param,
)
from services.cloud_auth_cookie import (
    bootstrap_cloud_auth_from_storage,
    clear_cloud_auth_cookie,
    read_cloud_auth_cookie,
    remember_cloud_auth,
    render_cloud_oauth_callback_capture,
    render_cloud_oauth_hash_capture_inline,
    sync_cloud_auth_browser_storage,
)
from services.cloud_state_helpers import (
    clear_scoped_finance_state as _clear_scoped_finance_state,
    set_cloud_auth as _set_cloud_auth,
    set_scope_owner as _set_scope_owner,
)
from services.cloud_sync_guard import (
    cloud_sync_ready_for_user,
    mark_cloud_sync_ready,
    payload_has_meaningful_data,
    payload_snapshot,
    pause_cloud_auto_sync,
    should_keep_local_data_before_auto_import,
    should_auto_create_cloud_copy_after_empty_remote,
    stored_snapshot_matches,
)
from services.device_state_storage import sync_device_state_browser_storage
from services.i18n import make_t, get_lang_code, is_rtl as _is_rtl
from services.oauth_pkce import (
    forget_pkce_flow,
    read_pkce_cookie,
    resolve_pkce_verifier,
)
from services.supabase_sync import SupabaseSyncClient


def _set_cloud_snapshot_now(user_id: str = "") -> None:
    snapshot = payload_snapshot(export_app_state_payload())
    st.session_state["_cloud_last_snapshot"] = snapshot
    st.session_state["_cloud_last_pull_user"] = str(user_id or "")


def _cloud_remote_check_due(user_id: str, *, interval_seconds: int = 8) -> bool:
    clean_user_id = str(user_id or "")
    if st.session_state.get("_cloud_remote_check_user") != clean_user_id:
        return True

    raw_checked_at = str(st.session_state.get("_cloud_remote_check_at") or "")
    if not raw_checked_at:
        return True

    try:
        checked_at = datetime.fromisoformat(raw_checked_at)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
    except Exception:
        return True

    return (datetime.now(timezone.utc) - checked_at).total_seconds() >= interval_seconds


def _mark_cloud_remote_checked(user_id: str) -> None:
    st.session_state["_cloud_remote_check_user"] = str(user_id or "")
    st.session_state["_cloud_remote_check_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")


def _apply_cloud_auth_session(
    client: SupabaseSyncClient,
    *,
    email: str,
    user_id: str,
    access_token: str,
    refresh_token: str,
    reason_suffix: str,
) -> bool:
    clean_user_id = str(user_id or "").strip()
    clean_email = str(email or "").strip()
    clean_access_token = str(access_token or "").strip()
    clean_refresh_token = str(refresh_token or "").strip()
    if not clean_user_id or not clean_access_token:
        return False

    previous_owner = ""
    scope = st.session_state.get("app_scope", {})
    if isinstance(scope, dict):
        previous_owner = str(scope.get("owner_user_id") or "")
    if previous_owner and previous_owner != clean_user_id:
        _clear_scoped_finance_state()

    _set_cloud_auth(
        True,
        email=clean_email,
        user_id=clean_user_id,
        access_token=clean_access_token,
        refresh_token=clean_refresh_token,
    )
    _set_scope_owner(clean_user_id, clean_email)
    if isinstance(st.session_state.get("settings"), dict):
        st.session_state.settings["cloud_sync_enabled"] = True
    remember_cloud_auth(clean_email, clean_user_id, clean_refresh_token)

    local_payload = export_app_state_payload()
    pull = client.fetch_user_data(clean_user_id, clean_access_token)
    remote_payload = pull.get("data") if isinstance(pull.get("data"), dict) else None

    if pull.get("ok") and remote_payload is not None:
        if should_keep_local_data_before_auto_import(local_payload, remote_payload):
            st.session_state["_cloud_last_snapshot"] = payload_snapshot(remote_payload)
            st.session_state["_cloud_last_pull_user"] = clean_user_id
            pause_cloud_auto_sync(
                st.session_state,
                clean_user_id,
                reason=f"local_cloud_conflict_after_{reason_suffix}",
            )
            save_persistent_state()
            return True

        import_app_state_payload(remote_payload)
        _set_scope_owner(clean_user_id, clean_email)
        _set_cloud_auth(
            True,
            email=clean_email,
            user_id=clean_user_id,
            access_token=clean_access_token,
            refresh_token=clean_refresh_token,
        )
        _set_cloud_snapshot_now(clean_user_id)
        mark_cloud_sync_ready(st.session_state, clean_user_id)
        if isinstance(st.session_state.get("settings"), dict):
            st.session_state.settings["cloud_sync_enabled"] = True
            st.session_state.settings["cloud_last_sync_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_persistent_state()
    elif pull.get("ok") and pull.get("data") is None:
        st.session_state["_cloud_last_snapshot"] = ""
        st.session_state["_cloud_last_pull_user"] = clean_user_id
        pause_cloud_auto_sync(
            st.session_state,
            clean_user_id,
            reason=f"cloud_empty_after_{reason_suffix}",
        )
        save_persistent_state()
    else:
        _set_cloud_snapshot_now(clean_user_id)
        pause_cloud_auto_sync(
            st.session_state,
            clean_user_id,
            reason=f"pull_failed_after_{reason_suffix}",
        )
        save_persistent_state()

    return True


def _query_param_value(name: str) -> str:
    try:
        value = st.query_params.get(name, "")
    except Exception:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def _clear_oauth_query_params() -> None:
    remove_keys = {
        "code",
        "state",
        "error",
        "error_code",
        "error_description",
        "cloud_oauth",
        "cloud_pkce_state",
    }
    try:
        remaining = {
            key: value
            for key, value in st.query_params.items()
            if str(key) not in remove_keys
        }
        st.query_params.clear()
        for key, value in remaining.items():
            st.query_params[key] = value
    except Exception:
        pass


def _handle_cloud_oauth_code_callback() -> None:
    auth_code = _query_param_value("code")
    auth_error = _query_param_value("error_description") or _query_param_value("error")
    if not auth_code and not auth_error:
        return

    oauth_provider = _query_param_value("cloud_oauth")
    if oauth_provider and oauth_provider != "apple":
        return

    st.session_state["current_page"] = "settings"
    st.session_state["settings_view"] = "data"

    if auth_error:
        st.session_state["_cloud_oauth_notice"] = {
            "type": "warning",
            "ar": f"لم يكتمل تسجيل الدخول بأبل: {auth_error}",
            "en": f"Apple sign-in did not complete: {auth_error}",
        }
        _clear_oauth_query_params()
        st.rerun()

    returned_state = _query_param_value("state")
    redirect_state = _query_param_value("cloud_pkce_state")
    auth_state = redirect_state or returned_state
    pkce_cookie_flow = read_pkce_cookie()
    code_verifier = resolve_pkce_verifier(
        auth_state,
        cookie_flow=pkce_cookie_flow,
        session_state=st.session_state,
    )
    st.session_state["_cloud_oauth_last_callback_debug"] = {
        "code_present": bool(auth_code),
        "state_present": bool(returned_state),
        "redirect_state_present": bool(redirect_state),
        "pkce_cookie_present": bool(pkce_cookie_flow),
        "pkce_session_present": bool(st.session_state.get("_cloud_oauth_pkce_flow")),
        "pkce_verifier_resolved": bool(code_verifier),
    }

    if not code_verifier:
        st.session_state["_cloud_oauth_notice"] = {
            "type": "warning",
            "ar": "رجع Apple برمز الدخول، لكن انتهت جلسة التحقق المؤقتة. جربي زر Apple مرة ثانية.",
            "en": "Apple returned with a sign-in code, but the temporary verification session expired. Try the Apple button again.",
        }
        _clear_oauth_query_params()
        st.rerun()

    client = SupabaseSyncClient.from_runtime(getattr(st, "secrets", None))
    if not client.is_configured:
        st.session_state["_cloud_oauth_notice"] = {
            "type": "warning",
            "ar": "إعدادات Supabase غير متاحة حاليًا.",
            "en": "Supabase configuration is not available right now.",
        }
        _clear_oauth_query_params()
        st.rerun()

    exchange = client.exchange_pkce_code(auth_code, code_verifier)
    if not exchange.get("ok"):
        st.session_state["_cloud_oauth_notice"] = {
            "type": "warning",
            "ar": f"تعذر إكمال تسجيل الدخول بأبل: {exchange.get('error') or 'OAuth failed'}",
            "en": f"Could not finish Apple sign-in: {exchange.get('error') or 'OAuth failed'}",
        }
        _clear_oauth_query_params()
        st.rerun()

    access_token = str(exchange.get("access_token") or "")
    refresh_token = str(exchange.get("refresh_token") or "")
    user_obj = exchange.get("user") if isinstance(exchange.get("user"), dict) else {}
    user_id = str(user_obj.get("id") or "")
    email = str(user_obj.get("email") or "")

    if not user_id and access_token:
        user_res = client.get_user(access_token)
        if user_res.get("ok") and isinstance(user_res.get("user"), dict):
            user_obj = user_res["user"]
            user_id = str(user_obj.get("id") or "")
            email = str(user_obj.get("email") or email)

    if not refresh_token or not _apply_cloud_auth_session(
        client,
        email=email,
        user_id=user_id,
        access_token=access_token,
        refresh_token=refresh_token,
        reason_suffix="sign_in",
    ):
        st.session_state["_cloud_oauth_notice"] = {
            "type": "warning",
            "ar": "رجعنا من Apple، لكن بيانات الجلسة ناقصة. جربي تسجيل الدخول مرة ثانية.",
            "en": "Apple returned, but the session data was incomplete. Try signing in again.",
        }
        _clear_oauth_query_params()
        st.rerun()

    st.session_state["_cloud_cookie_restore_checked"] = True
    st.session_state["_cloud_remember_login"] = True
    forget_pkce_flow(st.session_state)
    st.session_state.pop("_cloud_oauth_last_callback_debug", None)
    st.session_state["_cloud_oauth_notice"] = {
        "type": "success",
        "ar": "تم تسجيل الدخول بأبل وربط السحابة.",
        "en": "Apple sign-in is connected to cloud sync.",
    }
    _clear_oauth_query_params()
    st.rerun()


def _sync_cloud_auth_browser_bridge() -> tuple[dict, bool]:
    clear_requested = bool(st.session_state.pop("_cloud_browser_storage_clear_requested", False))
    cloud_auth = st.session_state.get("cloud_auth", {})
    remember_login = st.session_state.get("_cloud_remember_login")

    payload = None
    if (
        not clear_requested
        and isinstance(cloud_auth, dict)
        and cloud_auth.get("logged_in")
        and cloud_auth.get("refresh_token")
        and remember_login is not False
    ):
        payload = {
            "email": str(cloud_auth.get("email") or ""),
            "user_id": str(cloud_auth.get("user_id") or ""),
            "refresh_token": str(cloud_auth.get("refresh_token") or ""),
        }

    try:
        return sync_cloud_auth_browser_storage(payload, clear=clear_requested)
    except Exception:
        st.session_state["_cloud_browser_storage_last_error"] = "clear_failed" if clear_requested else "sync_failed"
        return {}, True


def _read_device_state_browser_bridge() -> tuple[dict, bool]:
    if _local_persistence_enabled():
        st.session_state["_device_browser_restore_checked"] = True
        return {}, True
    if st.session_state.get("_device_browser_restore_checked", False):
        return {}, True
    try:
        return sync_device_state_browser_storage(enabled=False, key="device_state_browser_bridge_read")
    except Exception:
        st.session_state["_device_browser_storage_last_error"] = "read_failed"
        return {}, True


def _restore_device_state_from_browser(browser_payload: dict | None, browser_storage_ready: bool) -> None:
    if st.session_state.get("_device_browser_restore_checked", False):
        return
    if not browser_storage_ready:
        return

    st.session_state["_device_browser_restore_checked"] = True
    if not isinstance(browser_payload, dict) or not browser_payload:
        return

    current_payload = export_app_state_payload()
    if payload_has_meaningful_data(current_payload):
        return

    import_app_state_payload(browser_payload)
    st.session_state["_persist_loaded"] = True
    st.rerun()


def _sync_device_state_browser_bridge() -> None:
    if _local_persistence_enabled():
        return
    if not st.session_state.get("_device_browser_restore_checked", False):
        return

    clear_requested = bool(st.session_state.pop("_device_browser_storage_clear_requested", False))
    settings = st.session_state.get("settings", {})
    device_save_enabled = not (isinstance(settings, dict) and settings.get("device_save_enabled") is False)

    if clear_requested or not device_save_enabled:
        try:
            sync_device_state_browser_storage(clear=True, key="device_state_browser_bridge_write")
        except Exception:
            st.session_state["_device_browser_storage_last_error"] = "clear_failed"
        return

    try:
        sync_device_state_browser_storage(
            export_app_state_payload(),
            enabled=True,
            key="device_state_browser_bridge_write",
        )
    except Exception:
        st.session_state["_device_browser_storage_last_error"] = "sync_failed"


def _restore_cloud_auth_from_cookie(browser_storage_auth: dict | None = None, browser_storage_ready: bool = True) -> None:
    if st.session_state.get("_cloud_cookie_restore_checked", False):
        return

    cloud_auth = st.session_state.get("cloud_auth", {})
    if isinstance(cloud_auth, dict) and cloud_auth.get("logged_in") and cloud_auth.get("access_token"):
        st.session_state["_cloud_cookie_restore_checked"] = True
        return

    remembered_auth = read_cloud_auth_cookie()
    if not remembered_auth:
        if not browser_storage_ready:
            return
        remembered_auth = browser_storage_auth or {}

    refresh_token = str(remembered_auth.get("refresh_token") or "").strip()
    if not refresh_token:
        st.session_state["_cloud_cookie_restore_checked"] = True
        return

    client = SupabaseSyncClient.from_runtime(getattr(st, "secrets", None))
    if not client.is_configured:
        st.session_state["_cloud_cookie_restore_checked"] = True
        return

    st.session_state["_cloud_cookie_restore_checked"] = True
    refreshed = client.refresh_session(refresh_token)
    if not refreshed.get("ok"):
        clear_cloud_auth_cookie()
        st.session_state["_cloud_browser_storage_clear_requested"] = True
        return

    access_token = str(refreshed.get("access_token") or "")
    new_refresh_token = str(refreshed.get("refresh_token") or refresh_token)
    user_obj = refreshed.get("user") if isinstance(refreshed.get("user"), dict) else {}
    user_id = str(user_obj.get("id") or remembered_auth.get("user_id") or "")
    email = str(user_obj.get("email") or remembered_auth.get("email") or "")

    if not access_token or not user_id:
        clear_cloud_auth_cookie()
        st.session_state["_cloud_browser_storage_clear_requested"] = True
        return

    previous_owner = ""
    scope = st.session_state.get("app_scope", {})
    if isinstance(scope, dict):
        previous_owner = str(scope.get("owner_user_id") or "")
    if previous_owner and previous_owner != user_id:
        _clear_scoped_finance_state()

    _set_cloud_auth(True, email=email, user_id=user_id, access_token=access_token, refresh_token=new_refresh_token)
    _set_scope_owner(user_id, email)
    if isinstance(st.session_state.get("settings"), dict):
        st.session_state.settings["cloud_sync_enabled"] = True
    remember_cloud_auth(email, user_id, new_refresh_token)

    local_payload = export_app_state_payload()
    pull = client.fetch_user_data(user_id, access_token)
    remote_payload = pull.get("data") if isinstance(pull.get("data"), dict) else None

    if pull.get("ok") and remote_payload is not None:
        if should_keep_local_data_before_auto_import(local_payload, remote_payload):
            st.session_state["_cloud_last_snapshot"] = payload_snapshot(remote_payload)
            st.session_state["_cloud_last_pull_user"] = user_id
            pause_cloud_auto_sync(st.session_state, user_id, reason="local_cloud_conflict_after_cookie_restore")
            save_persistent_state()
            return

        import_app_state_payload(remote_payload)
        _set_scope_owner(user_id, email)
        _set_cloud_auth(True, email=email, user_id=user_id, access_token=access_token, refresh_token=new_refresh_token)
        _set_cloud_snapshot_now(user_id)
        mark_cloud_sync_ready(st.session_state, user_id)
        if isinstance(st.session_state.get("settings"), dict):
            st.session_state.settings["cloud_sync_enabled"] = True
            st.session_state.settings["cloud_last_sync_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        save_persistent_state()
    elif pull.get("ok") and pull.get("data") is None:
        st.session_state["_cloud_last_snapshot"] = ""
        st.session_state["_cloud_last_pull_user"] = user_id
        pause_cloud_auto_sync(st.session_state, user_id, reason="cloud_empty_after_cookie_restore")
        save_persistent_state()
    else:
        _set_cloud_snapshot_now(user_id)
        pause_cloud_auto_sync(st.session_state, user_id, reason="pull_failed_after_cookie_restore")
        save_persistent_state()


def _sync_cloud_auth_cookie_preference() -> None:
    cloud_auth = st.session_state.get("cloud_auth", {})
    if not isinstance(cloud_auth, dict):
        return
    if not cloud_auth.get("logged_in") or not cloud_auth.get("refresh_token"):
        return
    if st.session_state.get("_cloud_remember_login") is False:
        return
    remember_cloud_auth(
        str(cloud_auth.get("email") or ""),
        str(cloud_auth.get("user_id") or ""),
        str(cloud_auth.get("refresh_token") or ""),
    )


def _sync_cloud_if_logged_in() -> None:
    cloud_auth = st.session_state.get("cloud_auth", {})
    if not isinstance(cloud_auth, dict):
        return

    settings = st.session_state.get("settings", {})
    if not isinstance(settings, dict):
        return

    if not bool(settings.get("cloud_sync_enabled", False)):
        return

    if str(settings.get("cloud_sync_mode", "auto") or "auto") == "manual":
        return

    if not cloud_auth.get("logged_in"):
        return

    user_id = str(cloud_auth.get("user_id") or "")
    access_token = str(cloud_auth.get("access_token") or "")
    if not user_id or not access_token:
        return

    app_scope = st.session_state.get("app_scope", {})
    owner_user_id = ""
    if isinstance(app_scope, dict):
        owner_user_id = str(app_scope.get("owner_user_id") or "")

    # Safety: never auto-push local data into a different signed-in user.
    if owner_user_id and owner_user_id != user_id:
        return

    client = SupabaseSyncClient.from_runtime(getattr(st, "secrets", None))
    if not client.is_configured:
        return

    # Refresh access token if it may be stale (issued >50 min ago).
    auth_issued = st.session_state.get("_cloud_auth_issued_at", "")
    if auth_issued:
        try:
            age_seconds = (datetime.now() - datetime.fromisoformat(auth_issued)).total_seconds()
        except Exception:
            age_seconds = 0
        if age_seconds > 3000:
            refresh_token = str(cloud_auth.get("refresh_token") or "")
            if refresh_token:
                refreshed = client.refresh_session(refresh_token)
                if refreshed.get("ok"):
                    access_token = str(refreshed.get("access_token") or access_token)
                    new_refresh = str(refreshed.get("refresh_token") or refresh_token)
                    _set_cloud_auth(True, email=str(cloud_auth.get("email") or ""), user_id=user_id, access_token=access_token, refresh_token=new_refresh)
                    st.session_state["_cloud_auth_issued_at"] = datetime.now().isoformat(timespec="seconds")
                else:
                    st.session_state["_cloud_sync_last_error"] = "token_refresh_failed"
                    pause_cloud_auto_sync(st.session_state, user_id, reason="token_refresh_failed")
                    return

    payload = export_app_state_payload()
    snapshot = payload_snapshot(payload)
    last_snapshot = str(st.session_state.get("_cloud_last_snapshot") or "")

    if cloud_sync_ready_for_user(st.session_state, user_id) and _cloud_remote_check_due(user_id):
        pull = client.fetch_user_data(user_id, access_token)
        _mark_cloud_remote_checked(user_id)

        remote_payload = pull.get("data") if isinstance(pull.get("data"), dict) else None
        if pull.get("ok") and remote_payload is not None:
            remote_snapshot = payload_snapshot(remote_payload)
            if remote_snapshot and remote_snapshot != snapshot:
                local_is_unchanged_since_last_sync = (
                    not payload_has_meaningful_data(payload)
                    or stored_snapshot_matches(last_snapshot, snapshot)
                )
                if local_is_unchanged_since_last_sync:
                    import_app_state_payload(remote_payload)
                    _set_scope_owner(user_id, str(cloud_auth.get("email") or ""))
                    _set_cloud_auth(
                        True,
                        email=str(cloud_auth.get("email") or ""),
                        user_id=user_id,
                        access_token=access_token,
                        refresh_token=str(cloud_auth.get("refresh_token") or ""),
                    )
                    mark_cloud_sync_ready(st.session_state, user_id)
                    st.session_state["_cloud_last_snapshot"] = remote_snapshot
                    st.session_state["_cloud_last_pull_user"] = user_id
                    st.session_state["_cloud_sync_last_error"] = ""
                    if isinstance(st.session_state.get("settings"), dict):
                        st.session_state.settings["cloud_sync_enabled"] = True
                        st.session_state.settings["cloud_last_sync_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    save_persistent_state()
                    st.rerun()

                st.session_state["_cloud_last_snapshot"] = remote_snapshot
                st.session_state["_cloud_last_pull_user"] = user_id
                pause_cloud_auto_sync(st.session_state, user_id, reason="local_cloud_conflict_after_auto_pull")
                save_persistent_state()
                return
        elif not pull.get("ok"):
            st.session_state["_cloud_sync_last_error"] = str(pull.get("error") or "sync_pull_failed")
            return

    if not cloud_sync_ready_for_user(st.session_state, user_id):
        if not should_auto_create_cloud_copy_after_empty_remote(st.session_state, payload):
            return

    if not snapshot:
        return

    if (
        stored_snapshot_matches(str(st.session_state.get("_cloud_last_snapshot") or ""), snapshot)
        and st.session_state.get("_cloud_last_pull_user") == user_id
    ):
        return

    push = client.upsert_user_data(user_id, access_token, payload)
    if push.get("ok"):
        st.session_state["_cloud_last_snapshot"] = snapshot
        st.session_state["_cloud_last_pull_user"] = user_id
        st.session_state["_cloud_sync_last_error"] = ""
        mark_cloud_sync_ready(st.session_state, user_id)
        if isinstance(settings, dict):
            settings["cloud_last_sync_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            st.session_state["settings"] = settings
    else:
        st.session_state["_cloud_sync_last_error"] = str(push.get("error") or "sync_push_failed")


def _render_native_shell_chrome_guard() -> None:
    if not _is_native_shell_runtime():
        return

    st.markdown(
        """
        <style id="goushfi-native-shell-chrome-guard">
        #stDecoration,
        [data-testid="stDecoration"],
        [data-testid="stStatusWidget"],
        [data-testid="stToolbar"],
        [data-testid="stDeployButton"],
        [data-testid="stAppDeployButton"],
        [data-testid="stMainMenu"],
        #MainMenu,
        footer,
        header[data-testid="stHeader"],
        div[class*="stDecoration"],
        div[class*="stStatusWidget"],
        div[class*="stToolbar"] {
            display: none !important;
            visibility: hidden !important;
            width: 0 !important;
            height: 0 !important;
            min-width: 0 !important;
            min-height: 0 !important;
            max-width: 0 !important;
            max-height: 0 !important;
            padding: 0 !important;
            margin: 0 !important;
            border: 0 !important;
            opacity: 0 !important;
            pointer-events: none !important;
            overflow: hidden !important;
        }
        </style>
        <script>
        (function() {
          const markNativeShell = () => {
            if (document.documentElement?.getAttribute("data-goushfi-native-shell") !== "1") {
              document.documentElement?.setAttribute("data-goushfi-native-shell", "1");
            }
            if (document.body?.getAttribute("data-goushfi-native-shell") !== "1") {
              document.body?.setAttribute("data-goushfi-native-shell", "1");
            }
          };

          const hideElement = (element) => {
            if (!element || !element.style) return;
            if (element.getAttribute("data-goushfi-chrome-hidden") === "1") return;
            element.setAttribute("data-goushfi-chrome-hidden", "1");
            element.style.setProperty("display", "none", "important");
            element.style.setProperty("visibility", "hidden", "important");
            element.style.setProperty("width", "0", "important");
            element.style.setProperty("height", "0", "important");
            element.style.setProperty("min-width", "0", "important");
            element.style.setProperty("min-height", "0", "important");
            element.style.setProperty("max-width", "0", "important");
            element.style.setProperty("max-height", "0", "important");
            element.style.setProperty("padding", "0", "important");
            element.style.setProperty("margin", "0", "important");
            element.style.setProperty("border", "0", "important");
            element.style.setProperty("opacity", "0", "important");
            element.style.setProperty("pointer-events", "none", "important");
            element.style.setProperty("overflow", "hidden", "important");
          };

          const fixedTopChrome = (element) => {
            try {
              const style = window.getComputedStyle(element);
              const rect = element.getBoundingClientRect();
              return (
                style.position === "fixed" &&
                rect.top <= 4 &&
                rect.left <= 2 &&
                rect.width >= window.innerWidth * 0.75 &&
                rect.height > 0 &&
                rect.height <= 8
              );
            } catch (error) {
              return false;
            }
          };

          const hideChrome = () => {
            markNativeShell();
            document.querySelectorAll(
              [
                "#stDecoration",
                "[data-testid='stDecoration']",
                "[data-testid='stStatusWidget']",
                "[data-testid='stToolbar']",
                "[data-testid='stDeployButton']",
                "[data-testid='stAppDeployButton']",
                "[data-testid='stMainMenu']",
                "#MainMenu",
                "footer",
                "header[data-testid='stHeader']",
                "div[class*='stDecoration']",
                "div[class*='stStatusWidget']",
                "div[class*='stToolbar']",
              ].join(",")
            ).forEach(hideElement);
            document.querySelectorAll("body > div, body > section, body > header").forEach((element) => {
              if (fixedTopChrome(element)) hideElement(element);
            });
          };

          hideChrome();
          window.addEventListener("load", hideChrome);
          const observer = new MutationObserver(hideChrome);
          observer.observe(document.documentElement, {
            attributes: true,
            childList: true,
            subtree: true,
          });
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )


def _render_page_loading_styles() -> None:
    st.markdown(
        """
        <style>
        div[data-testid="stSpinner"] > div {
            border-top-color: #1e293b !important;
        }
        div[data-testid="stSpinner"] [data-testid="stMarkdownContainer"],
        div[data-testid="stSpinner"] [data-testid="stMarkdownContainer"] * {
            color: #64748b !important;
            font-size: 0.95rem !important;
            animation: floosyPulse 1.5s infinite ease-in-out;
        }
        @keyframes floosyPulse {
            0%, 100% { opacity: 0.62; }
            50% { opacity: 1; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main():
    st.set_page_config(page_title="GoushFi", layout="wide")
    _render_native_shell_chrome_guard()

    if not st.session_state.get("_splash_shown"):
        _splash_logo = get_builtin_logo_b64()
        st.markdown(
            f"""
            <style>
            #goushfi-splash {{
                position:fixed;inset:0;z-index:999999;
                display:flex;flex-direction:column;align-items:center;justify-content:center;
                background:linear-gradient(135deg,#0f172a 0%,#1e293b 100%);
                animation:goushfi-fade 2.2s ease-in-out forwards;
            }}
            #goushfi-splash img {{height:108px;width:108px;border-radius:18px;margin-bottom:18px;}}
            #goushfi-splash .splash-name {{
                color:#fff;font-size:2rem;font-weight:800;letter-spacing:-0.03em;
            }}
            #goushfi-splash .splash-tag {{
                color:rgba(255,255,255,0.6);font-size:0.9rem;margin-top:6px;letter-spacing:0.08em;
            }}
            @keyframes goushfi-fade {{
                0%,60%{{opacity:1}} 100%{{opacity:0;pointer-events:none}}
            }}
            </style>
            <div id="goushfi-splash">
                <img src="{_splash_logo}" alt="GoushFi" />
                <div class="splash-name">GoushFi</div>
                <div class="splash-tag">Flow · Control · Growth</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.session_state["_splash_shown"] = True

    # تهيئة عامة (session_state + css إن كانت داخل config_floosy)
    init_session_state()
    device_storage_payload, device_storage_ready = _read_device_state_browser_bridge()
    _restore_device_state_from_browser(device_storage_payload, device_storage_ready)
    _handle_cloud_oauth_code_callback()
    render_cloud_oauth_hash_capture_inline()
    render_cloud_oauth_callback_capture()
    browser_storage_auth, browser_storage_ready = _sync_cloud_auth_browser_bridge()
    bootstrap_cloud_auth_from_storage()
    _restore_cloud_auth_from_cookie(browser_storage_auth, browser_storage_ready)
    _sync_cloud_auth_cookie_preference()

    # تحميل الصفحات بشكل آمن
    try:
        import pages_floosy.account_page as account_page
        import pages_floosy.assistant_page as assistant_page
        import pages_floosy.dashboard_page as dashboard_page
        import pages_floosy.mustndaty_page as mustndaty_page
        import pages_floosy.project_page as project_page
        import pages_floosy.savings_page as savings_page
        import pages_floosy.settings_page as settings_page
        import pages_floosy.tax_page as tax_page
    except Exception:
        st.error("في خطأ يمنع تشغيل التطبيق بسبب ImportError أو مشكلة في أحد الملفات.")
        st.code(traceback.format_exc())
        st.stop()

    t = make_t()
    lang_code = get_lang_code()
    is_en = lang_code == "en"
    lang_dir = "rtl" if _is_rtl() else "ltr"

    st.markdown(
        f"""
        <div
          id="floosy-language-marker"
          data-floosy-language="{lang_code}"
          data-floosy-dir="{lang_dir}"
          style="display:none !important;"
          aria-hidden="true"
        ></div>
        <script>
        (function() {{
          const html = document.documentElement;
          const body = document.body;
          if (html) {{
            html.lang = "{lang_code}";
            html.dir = "{lang_dir}";
            html.setAttribute("data-floosy-language", "{lang_code}");
          }}
          if (body) {{
            body.setAttribute("data-floosy-language", "{lang_code}");
            body.setAttribute("dir", "{lang_dir}");
          }}
          window.__floosyShellLanguage = "{lang_code}";
          try {{
            window.webkit?.messageHandlers?.floosyBridge?.postMessage({{
              type: "language",
              language: "{lang_code}",
              dir: "{lang_dir}",
              source: "page-script",
            }});
          }} catch (error) {{}}
          window.dispatchEvent(new CustomEvent("floosy-language-change", {{
            detail: {{
              language: "{lang_code}",
              dir: "{lang_dir}",
            }},
          }}));
        }})();
        </script>
        """,
        unsafe_allow_html=True,
    )

    page_labels = {
        "home": t("الرئيسية", "Home"),
        "account": t("الحساب", "My Account"),
        "savings": t("التوفير", "Savings"),
        "assistant": t("المحلل المالي", "Financial Analyzer"),
        "documents": t("مستنداتي", "Documents"),
        "tax": t("الفواتير والضرائب", "Invoices & Tax"),
        "project": t("المشاريع", "Projects"),
        "settings": t("الإعدادات", "Settings"),
    }

    if "current_page" not in st.session_state:
        st.session_state.current_page = "home"

    legacy_map = {
        "الرئيسية": "home",
        "الحساب": "account",
        "التوفير": "savings",
        "المحلل المالي": "assistant",
        "المساعد الذكي": "assistant",
        "مستنداتي": "documents",
        "مشروع صغير": "project",
        "الإعدادات": "settings",
        "الالتزامات": "account",
        "Home": "home",
        "Account": "account",
        "My Account": "account",
        "Savings": "savings",
        "Financial Analyzer": "assistant",
        "Documents": "documents",
        "الفواتير والضرائب": "tax",
        "Invoices & Tax": "tax",
        "Projects": "project",
        "Settings": "settings",
    }
    st.session_state.current_page = legacy_map.get(st.session_state.current_page, st.session_state.current_page)

    # Query params are only an entry point for shared links. Updating them after
    # every sidebar click causes an extra Streamlit rerun, which can swallow the
    # first navigation click on hosted builds.
    query_page_applied = False
    if not st.session_state.get("_nav_initial_query_page_applied", False):
        requested_page = ""
        try:
            requested_page = str(st.query_params.get("page", "") or "").strip()
        except Exception:
            requested_page = ""
        requested_page = legacy_map.get(requested_page, requested_page)
        if requested_page in page_labels:
            st.session_state.current_page = requested_page
            query_page_applied = True
            clear_regular_web_page_query_param()
        st.session_state["_nav_initial_query_page_applied"] = True

    if st.session_state.current_page not in page_labels:
        st.session_state.current_page = "home"

    page_keys = list(page_labels.keys())
    selected_key = st.session_state.current_page

    if not _is_native_shell_runtime():
        st.sidebar.title("GoushFi")
        sidebar_radio_key = "sidebar_section"
        sidebar_value = legacy_map.get(
            str(st.session_state.get(sidebar_radio_key, "") or "").strip(),
            str(st.session_state.get(sidebar_radio_key, "") or "").strip(),
        )

        if query_page_applied:
            st.session_state[sidebar_radio_key] = st.session_state.current_page
        elif sidebar_value in page_labels:
            st.session_state.current_page = sidebar_value
        elif sidebar_radio_key in st.session_state:
            st.session_state[sidebar_radio_key] = st.session_state.current_page

        radio_kwargs = {
            "key": sidebar_radio_key,
            "format_func": lambda page_key: page_labels[page_key],
        }
        if sidebar_radio_key not in st.session_state:
            radio_kwargs["index"] = page_keys.index(st.session_state.current_page)

        selected_key = st.sidebar.radio(
            t("القسم", "Section"),
            page_keys,
            **radio_kwargs,
        )
        selected_key = legacy_map.get(selected_key, selected_key)
        if selected_key not in page_labels:
            selected_key = st.session_state.current_page

    st.session_state.current_page = selected_key
    # اختيار الشهر/السنة (صفحات تحتاجها)
    month_key, month, year = get_month_selection(selected_key)

    _render_page_loading_styles()
    loading_msg = t("جاري تحميل الصفحة...", "Loading page...")
    with st.spinner(loading_msg):
        # صفحات ما تحتاج شهر
        if selected_key == "settings":
            settings_page.render()
        elif selected_key == "documents":
            mustndaty_page.render()
        else:
            # باقي الصفحات تحتاج month_key
            ensure_month_keys(month_key)

            if selected_key == "home":
                dashboard_page.render(month_key, month, year)
            elif selected_key == "account":
                account_page.render(month_key, month, year)
            elif selected_key == "savings":
                savings_page.render(month_key, month, year)
            elif selected_key == "assistant":
                assistant_page.render(month_key, month, year)
            elif selected_key == "tax":
                tax_page.render(month_key, month, year)
            elif selected_key == "project":
                project_page.render(month_key, month, year)

    save_persistent_state()
    _sync_cloud_if_logged_in()
    _sync_device_state_browser_bridge()


# Streamlit runs top-to-bottom, so call main() directly.
main()

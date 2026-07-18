from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from uuid import UUID


DEMO_USER_ID_ENV = "GOUSHFI_BUILD_WEEK_DEMO_USER_ID"
FORCE_WEB_ENV = "GOUSHFI_BUILD_WEEK_FORCE_WEB"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _mapping_value(source: Any, key: str) -> Any:
    if not isinstance(source, Mapping):
        return None
    try:
        return source.get(key)
    except Exception:
        return None


def _text_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def _query_value(query_params: Any, key: str) -> str:
    return _text_value(_mapping_value(query_params, key))


def _normalized_uuid(value: Any) -> str:
    clean_value = _text_value(value)
    if not clean_value:
        return ""
    try:
        return str(UUID(clean_value))
    except (TypeError, ValueError, AttributeError):
        return ""


def is_ios_build_week_request(query_params: Any, user_agent: str) -> bool:
    """
    Identify the current iPhone/iPad WebView request used by the native app.

    ``f_w=1`` never enables Build Week mode. It is considered here only with
    the native shell marker and an iOS user agent so it cannot act as access
    control on its own.
    """

    user_agent_lower = _text_value(user_agent).casefold()
    is_ios = any(marker in user_agent_lower for marker in ("iphone", "ipad", "ipod"))
    return (
        is_ios
        and _query_value(query_params, "f_w") == "1"
        and _query_value(query_params, "f_shell") == "1"
    )


def is_verified_demo_account(cloud_auth: Any, app_scope: Any, demo_user_id: Any) -> bool:
    """Require a live authenticated session whose scoped owner is the one demo UUID."""

    if not isinstance(cloud_auth, Mapping) or not isinstance(app_scope, Mapping):
        return False
    if _mapping_value(cloud_auth, "logged_in") is not True:
        return False
    if not _text_value(_mapping_value(cloud_auth, "access_token")):
        return False

    configured_demo_id = _normalized_uuid(demo_user_id)
    authenticated_user_id = _normalized_uuid(_mapping_value(cloud_auth, "user_id"))
    scoped_owner_id = _normalized_uuid(_mapping_value(app_scope, "owner_user_id"))
    return bool(
        configured_demo_id
        and authenticated_user_id == configured_demo_id
        and scoped_owner_id == configured_demo_id
    )


def should_show_financial_calm_brief(
    *,
    cloud_auth: Any,
    app_scope: Any,
    query_params: Any,
    user_agent: str = "",
    environ: Mapping[str, str] | None = None,
) -> bool:
    """
    Fail-closed Build Week gate for Financial Calm Brief.

    ``GOUSHFI_BUILD_WEEK_FORCE_WEB`` is an internal web-preview escape hatch.
    Operations must keep it disabled (``0`` or unset) in judging and production.
    """

    runtime_env = os.environ if environ is None else environ

    # The iOS deny rule wins over both demo eligibility and internal preview.
    if is_ios_build_week_request(query_params, user_agent):
        return False

    force_web = _text_value(_mapping_value(runtime_env, FORCE_WEB_ENV)).casefold() in _TRUE_VALUES
    if force_web:
        return True

    demo_user_id = _mapping_value(runtime_env, DEMO_USER_ID_ENV)
    return is_verified_demo_account(cloud_auth, app_scope, demo_user_id)

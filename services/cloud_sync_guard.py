from __future__ import annotations

import json


READY_USER_KEY = "_cloud_sync_ready_user"
PAUSE_REASON_KEY = "_cloud_sync_pause_reason"
EMPTY_REMOTE_REASONS = {"cloud_empty_after_sign_in", "cloud_empty_after_cookie_restore"}


def mark_cloud_sync_ready(session_state, user_id: str) -> None:
    session_state[READY_USER_KEY] = str(user_id or "")
    session_state[PAUSE_REASON_KEY] = ""


def pause_cloud_auto_sync(session_state, user_id: str = "", reason: str = "") -> None:
    session_state[READY_USER_KEY] = ""
    session_state[PAUSE_REASON_KEY] = str(reason or "").strip()


def clear_cloud_sync_guard(session_state) -> None:
    session_state[READY_USER_KEY] = ""
    session_state[PAUSE_REASON_KEY] = ""


def cloud_sync_ready_for_user(session_state, user_id: str) -> bool:
    return str(session_state.get(READY_USER_KEY) or "") == str(user_id or "")


def cloud_sync_pause_reason(session_state) -> str:
    return str(session_state.get(PAUSE_REASON_KEY) or "").strip()


def _payload_for_snapshot(payload: dict) -> dict:
    # Top-level underscore keys are payload metadata, not user finance data.
    return {key: value for key, value in payload.items() if not str(key).startswith("_")}


def payload_snapshot(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    try:
        return json.dumps(_payload_for_snapshot(payload), ensure_ascii=False, sort_keys=True)
    except Exception:
        return ""


def payload_has_meaningful_data(payload) -> bool:
    if not isinstance(payload, dict):
        return False

    transactions = payload.get("transactions")
    if isinstance(transactions, dict) and any(bool(v) for v in transactions.values()):
        return True

    savings = payload.get("savings")
    if isinstance(savings, dict) and any(bool(v) for v in savings.values()):
        return True

    project_data = payload.get("project_data")
    if isinstance(project_data, dict) and any(bool(v) for v in project_data.values()):
        return True

    recurring = payload.get("recurring")
    if isinstance(recurring, dict) and recurring.get("items"):
        return True

    documents = payload.get("documents")
    if isinstance(documents, list) and documents:
        return True

    invoices = payload.get("invoices")
    if isinstance(invoices, list) and invoices:
        return True

    return False


def should_auto_create_cloud_copy_after_empty_remote(session_state, payload) -> bool:
    reason = cloud_sync_pause_reason(session_state)
    return reason in EMPTY_REMOTE_REASONS and payload_has_meaningful_data(payload)


def should_keep_local_data_before_auto_import(local_payload, remote_payload) -> bool:
    if not payload_has_meaningful_data(local_payload):
        return False
    if not isinstance(remote_payload, dict):
        return False

    local_snapshot = payload_snapshot(local_payload)
    remote_snapshot = payload_snapshot(remote_payload)
    if not local_snapshot or not remote_snapshot:
        return False

    return local_snapshot != remote_snapshot

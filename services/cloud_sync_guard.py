from __future__ import annotations

import json
from numbers import Number


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


def stored_snapshot_matches(stored_snapshot: str, current_snapshot: str) -> bool:
    clean_stored = str(stored_snapshot or "").strip()
    clean_current = str(current_snapshot or "").strip()
    if not clean_stored or not clean_current:
        return False
    if clean_stored == clean_current:
        return True
    try:
        stored_payload = json.loads(clean_stored)
    except Exception:
        return False
    return payload_snapshot(stored_payload) == clean_current


def _nonzero_number(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, Number):
        return float(value) != 0.0
    try:
        clean_value = str(value or "").strip().replace(",", "")
        if not clean_value:
            return False
        return float(clean_value) != 0.0
    except Exception:
        return False


def _has_text(value) -> bool:
    return bool(str(value or "").strip())


def _transaction_is_meaningful(item) -> bool:
    if not isinstance(item, dict):
        return bool(item)

    for amount_key in ("amount", "value", "paid_amount", "total", "subtotal", "net", "gross"):
        if amount_key in item and _nonzero_number(item.get(amount_key)):
            return True

    text_keys = ("note", "notes", "description", "title", "name", "merchant", "client")
    return any(_has_text(item.get(key)) for key in text_keys)


def _record_is_meaningful(item) -> bool:
    if not isinstance(item, dict):
        return bool(item)

    for amount_key in ("amount", "value", "total", "subtotal", "balance", "goal", "net", "gross"):
        if amount_key in item and _nonzero_number(item.get(amount_key)):
            return True

    for text_key in (
        "name",
        "project_name",
        "business_name",
        "title",
        "note",
        "budget_note",
        "notes",
        "description",
        "number",
        "client",
        "file_name",
        "url",
    ):
        if _has_text(item.get(text_key)):
            return True

    for nested_key in ("items", "transactions", "project_transactions", "assets", "licenses", "documents"):
        nested_value = item.get(nested_key)
        if isinstance(nested_value, list) and any(_record_is_meaningful(nested_item) for nested_item in nested_value):
            return True

    nested_projects = item.get("projects")
    if isinstance(nested_projects, dict):
        return any(_record_is_meaningful(project) for project in nested_projects.values())

    return False


def _list_has_meaningful_items(value) -> bool:
    return isinstance(value, list) and any(_record_is_meaningful(item) for item in value)


def _month_map_has_meaningful_transactions(value) -> bool:
    if not isinstance(value, dict):
        return False
    for month_items in value.values():
        if isinstance(month_items, list) and any(_transaction_is_meaningful(item) for item in month_items):
            return True
    return False


def _savings_has_meaningful_data(value) -> bool:
    if not isinstance(value, dict):
        return False
    for month_data in value.values():
        if not isinstance(month_data, dict):
            if _record_is_meaningful(month_data):
                return True
            continue
        if _nonzero_number(month_data.get("goal")):
            return True
        transactions = month_data.get("transactions")
        if isinstance(transactions, list) and any(_transaction_is_meaningful(item) for item in transactions):
            return True
    return False


def _project_data_has_meaningful_data(value) -> bool:
    if not isinstance(value, dict):
        return False
    return any(_record_is_meaningful(month_data) for month_data in value.values())


def payload_has_meaningful_data(payload) -> bool:
    if not isinstance(payload, dict):
        return False

    transactions = payload.get("transactions")
    if _month_map_has_meaningful_transactions(transactions):
        return True

    savings = payload.get("savings")
    if _savings_has_meaningful_data(savings):
        return True

    project_data = payload.get("project_data")
    if _project_data_has_meaningful_data(project_data):
        return True

    recurring = payload.get("recurring")
    if isinstance(recurring, dict) and _list_has_meaningful_items(recurring.get("items")):
        return True

    documents = payload.get("documents")
    if _list_has_meaningful_items(documents):
        return True

    invoices = payload.get("invoices")
    if _list_has_meaningful_items(invoices):
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

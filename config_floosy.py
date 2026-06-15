import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import os
import json
import base64
from urllib.parse import urlparse
from services.currency_localization import currency_matches
from services.local_store import delete_sqlite_payload, load_sqlite_payload, save_sqlite_payload
from services.expense_tax_service import ExpenseTaxService

# ============ ثوابت عامة ============

CURRENCY_OPTIONS = [
    "د.ك - دينار كويتي",
    "ر.س - ريال سعودي",
    "د.إ - درهم إماراتي",
    "$ - دولار أمريكي",
    "€ - يورو",
    "¥ - 人民币",
    "₩ - 원",
    "¥ - 円",
    "Rp - Rupiah",
    "S$ - SGD",
]

FIXED_EXPENSE_CATEGORIES = ["إيجار / قسط", "فواتير"]
FIXED_INCOME_CATEGORIES = ["راتب"]
EXTRA_INCOME_CATEGORIES = ["دخل إضافي"]

arabic_months = [
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
]
english_months = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

default_settings = {
    "name": "",
    "default_currency": CURRENCY_OPTIONS[0],
    "language": "العربية",
    "language_user_selected": False,
    "profile_image": None,
    "show_status_account": True,
    "show_status_saving": True,
    "show_status_project": True,
    "device_save_enabled": True,
    "cloud_sync_enabled": False,
    "cloud_last_sync_at": "",
}

PLAN_DEFINITIONS = {
    "beta_free": {
        "label_ar": "مجاني",
        "label_en": "Free",
        "features": {
            "account": True,
            "savings": True,
            "projects": True,
            "documents": True,
            "financial_analyzer": True,
            "cloud_sync": True,
        },
    },
    "free": {
        "label_ar": "مجاني",
        "label_en": "Free",
        "features": {
            "account": True,
            "savings": False,
            "projects": False,
            "documents": False,
            "financial_analyzer": False,
            "cloud_sync": True,
        },
    },
    "plus": {
        "label_ar": "بلس",
        "label_en": "Plus",
        "features": {
            "account": True,
            "savings": True,
            "projects": False,
            "documents": True,
            "financial_analyzer": True,
            "cloud_sync": True,
        },
    },
    "pro": {
        "label_ar": "برو",
        "label_en": "Pro",
        "features": {
            "account": True,
            "savings": True,
            "projects": True,
            "documents": True,
            "financial_analyzer": True,
            "cloud_sync": True,
        },
    },
}

default_plan_info = {
    "tier": "beta_free",
    "status": "active",
    "started_at": "",
    "updated_at": "",
}

PERSIST_DIR = "data"
PERSIST_FILE = os.path.join(PERSIST_DIR, "floosy_data.json")
PERSIST_SQLITE_FILE = os.path.join(PERSIST_DIR, "floosy_data.sqlite3")
PERSIST_KEYS = ["settings", "transactions", "savings", "project_data", "recurring", "documents", "plan_info", "invoices", "tax_profile", "tax_tags", "app_scope"]


def _normalize_plan_info(plan_info: dict) -> dict:
    now_iso = datetime.now().isoformat(timespec="seconds")
    normalized = default_plan_info.copy()
    if isinstance(plan_info, dict):
        normalized.update(plan_info)

    tier = str(normalized.get("tier") or "beta_free").strip().lower()
    if tier not in PLAN_DEFINITIONS:
        tier = "beta_free"
    normalized["tier"] = tier

    status = str(normalized.get("status") or "active").strip().lower()
    normalized["status"] = status or "active"

    if not str(normalized.get("started_at") or "").strip():
        normalized["started_at"] = now_iso
    if not str(normalized.get("updated_at") or "").strip():
        normalized["updated_at"] = now_iso

    return normalized


def get_plan_info() -> dict:
    current = st.session_state.get("plan_info")
    normalized = _normalize_plan_info(current if isinstance(current, dict) else {})
    st.session_state["plan_info"] = normalized
    return normalized


def get_plan_features(tier: str | None = None) -> dict:
    tier_key = (tier or get_plan_info().get("tier") or "beta_free").strip().lower()
    if tier_key not in PLAN_DEFINITIONS:
        tier_key = "beta_free"
    return PLAN_DEFINITIONS[tier_key]["features"].copy()


def plan_allows(feature_key: str, tier: str | None = None) -> bool:
    return bool(get_plan_features(tier=tier).get(feature_key, False))


def reset_local_app_data() -> None:
    st.session_state["settings"] = default_settings.copy()
    st.session_state["transactions"] = {}
    st.session_state["savings"] = {}
    st.session_state["project_data"] = {}
    st.session_state["recurring"] = {"items": []}
    st.session_state["documents"] = []
    st.session_state["mustndaty_documents"] = st.session_state["documents"]
    st.session_state["plan_info"] = _normalize_plan_info(default_plan_info.copy())
    st.session_state["invoices"] = []
    st.session_state["tax_profile"] = {}
    st.session_state["tax_tags"] = []
    st.session_state["app_scope"] = {"owner_user_id": "", "owner_email": ""}

    st.session_state["_persist_last_snapshot"] = ""
    st.session_state["_cloud_last_snapshot"] = ""
    st.session_state["_cloud_last_pull_user"] = ""

    for path in [PERSIST_FILE, PERSIST_FILE + ".tmp"]:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    delete_sqlite_payload(PERSIST_SQLITE_FILE)


def delete_local_persistent_copy() -> None:
    """Delete the saved local copy without clearing the current session."""
    for path in [PERSIST_FILE, PERSIST_FILE + ".tmp"]:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    delete_sqlite_payload(PERSIST_SQLITE_FILE)
    st.session_state["_persist_last_snapshot"] = ""


def _encode_for_json(value):
    if isinstance(value, (bytes, bytearray)):
        return {"__bytes_b64__": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): _encode_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode_for_json(v) for v in value]
    return value


def _decode_from_json(value):
    if isinstance(value, dict):
        if set(value.keys()) == {"__bytes_b64__"}:
            raw = value.get("__bytes_b64__", "")
            if isinstance(raw, str):
                try:
                    return base64.b64decode(raw.encode("ascii"))
                except Exception:
                    return None
        return {k: _decode_from_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode_from_json(v) for v in value]
    return value



def _persist_backend() -> str:
    raw_backend = str(os.getenv("FLOOSY_PERSIST_BACKEND", "sqlite") or "sqlite").strip().lower()
    return "json" if raw_backend == "json" else "sqlite"


_ACCEPT_LANG_MAP = {
    "ar": "العربية",
    "en": "English",
    "zh": "中文",
    "ko": "한국어",
    "ja": "日本語",
    "id": "Bahasa Indonesia",
    "ms": "Bahasa Melayu",
}


def _preferred_language_from_accept_language(header_value: str) -> str:
    raw_value = str(header_value or "").strip().lower()
    if not raw_value:
        return "العربية"

    for part in raw_value.split(","):
        token = part.split(";", 1)[0].strip()
        for prefix, lang_name in _ACCEPT_LANG_MAP.items():
            if token.startswith(prefix):
                return lang_name

    return "العربية"


def _read_browser_preferences_from_query_params() -> dict:
    try:
        query_params = st.query_params
    except Exception:
        return {}

    welcome_done = str(query_params.get("f_w", "") or "").strip() == "1"
    lang_code = str(query_params.get("f_lang", "") or "").strip().lower()
    language = _ACCEPT_LANG_MAP.get(lang_code, "")

    return {
        "completed": welcome_done,
        "language": language,
    }


def _apply_browser_query_preferences() -> None:
    settings = st.session_state.get("settings")
    if not isinstance(settings, dict):
        return

    prefs = _read_browser_preferences_from_query_params()
    if not prefs.get("completed"):
        return

    query_language = str(prefs.get("language", "") or "").strip()
    if query_language in set(_ACCEPT_LANG_MAP.values()):
        settings["language"] = query_language
        settings["language_user_selected"] = True

    st.session_state["settings"] = settings
    st.session_state["_welcome_completed"] = True


def sync_browser_preferences_state(
    *,
    language: str = "",
    name: str = "",
    welcome_done: bool = True,
) -> None:
    del name
    _name_to_code = {v: k for k, v in _ACCEPT_LANG_MAP.items()}
    lang_code = _name_to_code.get(language, "")

    try:
        current_page = str(st.query_params.get("page", "") or "").strip()
        current_shell = str(st.query_params.get("f_shell", "") or "").strip()
        st.query_params["f_w"] = "1" if welcome_done else "0"
        if lang_code:
            st.query_params["f_lang"] = lang_code
        elif "f_lang" in st.query_params:
            del st.query_params["f_lang"]
        if current_shell:
            st.query_params["f_shell"] = current_shell
        elif "f_shell" in st.query_params:
            del st.query_params["f_shell"]
        if current_page and current_shell:
            st.query_params["page"] = current_page
        elif "page" in st.query_params:
            del st.query_params["page"]
    except Exception:
        return


def clear_regular_web_page_query_param() -> None:
    """Keep mobile shell deep links, but do not let web page links stick forever."""
    try:
        current_shell = str(st.query_params.get("f_shell", "") or "").strip()
        if not current_shell and "page" in st.query_params:
            del st.query_params["page"]
    except Exception:
        return


def _detect_browser_language() -> str:
    try:
        context = getattr(st, "context", None)
    except Exception:
        context = None

    if context is None:
        return "العربية"

    try:
        headers = getattr(context, "headers", {}) or {}
    except Exception:
        headers = {}

    raw_value = ""
    if hasattr(headers, "get"):
        raw_value = str(headers.get("accept-language") or headers.get("Accept-Language") or "").strip()
    if not raw_value and isinstance(headers, dict):
        normalized_headers = {str(k).lower(): v for k, v in headers.items()}
        raw_value = str(normalized_headers.get("accept-language") or "").strip()

    return _preferred_language_from_accept_language(raw_value)


def _apply_browser_language_preference() -> None:
    settings = st.session_state.get("settings")
    if not isinstance(settings, dict):
        return

    if bool(settings.get("language_user_selected", False)):
        return

    detected_language = _detect_browser_language()
    if detected_language not in set(_ACCEPT_LANG_MAP.values()):
        return

    if settings.get("language") == detected_language:
        return

    settings["language"] = detected_language
    st.session_state["settings"] = settings


def _apply_language_direction_theme() -> None:
    settings = st.session_state.get("settings", {})
    if not isinstance(settings, dict):
        settings = {}

    _rtl_langs = {"العربية"}
    is_rtl_lang = settings.get("language", "العربية") in _rtl_langs
    direction = "rtl" if is_rtl_lang else "ltr"
    align = "right" if is_rtl_lang else "left"
    sidebar_side_css = ""
    if is_rtl_lang:
        sidebar_side_css = """
        section[data-testid="stSidebar"],
        [data-testid="stSidebar"],
        .stSidebar {
            left: auto !important;
            right: 0 !important;
            border-right: none !important;
            border-left: 1px solid rgba(255,255,255,0.14) !important;
            transition: transform 0.22s ease !important;
            min-width: 16rem !important;
            width: 16rem !important;
            max-width: min(16rem, 88vw) !important;
            box-sizing: border-box !important;
        }

        section[data-testid="stSidebar"][aria-expanded="true"],
        .stSidebar[aria-expanded="true"] {
            transform: translateX(0) !important;
        }

        section[data-testid="stSidebar"][aria-expanded="false"],
        .stSidebar[aria-expanded="false"] {
            transform: translateX(100%) !important;
            border-left: none !important;
        }

        [data-testid="collapsedControl"],
        [data-testid="stSidebarCollapsedControl"],
        button[title="Open sidebar"],
        button[aria-label="Open sidebar"] {
            left: auto !important;
            right: 0.75rem !important;
        }
        """

    st.markdown(
        f"""
        <style>
        .stApp,
        .main .block-container,
        [data-testid="stSidebar"] > div:first-child {{
            direction: {direction};
            text-align: {align};
        }}

        .flossy-header-inner {{
            direction: ltr;
            flex-direction: row;
        }}

        h1, h2, h3, h4, h5, h6,
        p,
        label,
        .stCaption,
        [data-testid="stMarkdownContainer"],
        [data-testid="stAlert"] {{
            text-align: {align};
        }}

        .flossy-header-title,
        .flossy-header-tagline {{
            direction: ltr;
            text-align: left;
        }}

        [data-testid="stSidebar"] .stRadio,
        [data-testid="stSidebar"] .stSelectbox,
        [data-testid="stSidebar"] .stNumberInput,
        [data-testid="stSidebar"] .stDateInput,
        [data-testid="stSidebar"] .stMarkdown,
        [data-testid="stSidebar"] label {{
            text-align: {align};
            direction: {direction};
        }}

        .stTextInput input,
        .stTextArea textarea,
        .stDateInput input,
        .stSelectbox [data-baseweb="select"] input,
        .stSelectbox [data-baseweb="select"] > div,
        .stMultiSelect [data-baseweb="select"] input,
        .stMultiSelect [data-baseweb="select"] > div {{
            direction: {direction};
            text-align: {align};
        }}

        .stNumberInput input {{
            direction: ltr;
            text-align: {align};
        }}

        div[data-testid="stDataFrame"],
        div[data-testid="stDataEditor"] {{
            direction: ltr;
        }}

        div[data-testid="stMetric"] {{
            text-align: {align};
        }}

        div[data-testid="stMetricLabel"] > div,
        div[data-testid="stMetricValue"] > div {{
            text-align: {align};
        }}

        {sidebar_side_css}

        section[data-testid="stSidebar"][aria-expanded="false"],
        [data-testid="stSidebar"][aria-expanded="false"],
        .stSidebar[aria-expanded="false"] {{
            min-width: 0 !important;
            width: 0 !important;
            max-width: 0 !important;
            flex: 0 0 0 !important;
            padding: 0 !important;
            border: 0 !important;
            overflow: hidden !important;
        }}

        </style>
        """,
        unsafe_allow_html=True,
    )


def _is_shared_hosted_url(url: str) -> bool:
    clean_url = str(url or "").strip().lower()
    if not clean_url:
        return False
    try:
        host = (urlparse(clean_url).hostname or "").strip().lower()
    except Exception:
        return False
    return (
        host.endswith(".streamlit.app")
        or host == "share.streamlit.io"
        or host.endswith(".share.streamlit.io")
    )


def _is_local_runtime_url(url: str) -> bool:
    clean_url = str(url or "").strip().lower()
    if not clean_url:
        return False
    try:
        host = (urlparse(clean_url).hostname or "").strip().lower()
    except Exception:
        return False
    return host in {"localhost", "127.0.0.1", "0.0.0.0"}


def _hosted_data_warning_state(runtime_url: str, cloud_configured: bool, cloud_logged_in: bool) -> str:
    if not _is_shared_hosted_url(runtime_url):
        return ""
    if bool(cloud_configured) and bool(cloud_logged_in):
        return ""
    if bool(cloud_configured):
        return "cloud_login_required"
    return "cloud_setup_required"


def _local_persistence_enabled() -> bool:
    explicit = str(os.getenv("FLOOSY_ENABLE_LOCAL_PERSISTENCE", "") or "").strip().lower()
    if explicit in {"1", "true", "yes", "on"}:
        return True
    if explicit in {"0", "false", "no", "off"}:
        return False

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
                headers = getattr(context, "headers", {})
                runtime_host = str(headers.get("host", "") or "")
                if runtime_host:
                    runtime_url = f"https://{runtime_host}"
            except Exception:
                runtime_url = ""

    if _is_local_runtime_url(runtime_url):
        return True
    if _is_shared_hosted_url(runtime_url):
        return False

    # Safe default for public beta: avoid shared file persistence unless explicitly enabled.
    return False


def _load_json_payload_from_file() -> dict | None:
    if not os.path.exists(PERSIST_FILE):
        return None

    try:
        with open(PERSIST_FILE, "r", encoding="utf-8") as f:
            raw_payload = json.load(f)
    except Exception:
        return None

    return raw_payload if isinstance(raw_payload, dict) else None


def export_app_state_payload() -> dict:
    """Export app data keys as JSON-safe payload."""
    payload = {
        key: _encode_for_json(st.session_state.get(key))
        for key in PERSIST_KEYS
        if key in st.session_state
    }
    payload["_schema_version"] = 1
    return payload


def import_app_state_payload(raw_payload) -> None:
    """Import app payload into session_state (supports encoded bytes)."""
    decoded = _decode_from_json(raw_payload)
    if not isinstance(decoded, dict):
        return

    docs = decoded.get("documents")
    legacy_docs = decoded.get("mustndaty_documents")
    if not isinstance(docs, list) and isinstance(legacy_docs, list):
        decoded["documents"] = legacy_docs

    for key in PERSIST_KEYS:
        if key in decoded:
            st.session_state[key] = decoded[key]


def load_persistent_state() -> None:
    if not _local_persistence_enabled():
        return

    backend = _persist_backend()

    raw_payload = None
    if backend == "sqlite":
        raw_payload = load_sqlite_payload(PERSIST_SQLITE_FILE)
        if raw_payload is None:
            # One-time migration path from JSON file to SQLite.
            raw_payload = _load_json_payload_from_file()
            if isinstance(raw_payload, dict):
                save_sqlite_payload(PERSIST_SQLITE_FILE, raw_payload)
    else:
        raw_payload = _load_json_payload_from_file()

    if isinstance(raw_payload, dict):
        import_app_state_payload(raw_payload)


def save_persistent_state() -> None:
    if not _local_persistence_enabled():
        return

    settings = st.session_state.get("settings", {})
    if isinstance(settings, dict) and settings.get("device_save_enabled") is False:
        return

    payload = export_app_state_payload()
    payload["_meta"] = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "version": 1,
    }

    try:
        compact_snapshot = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return

    if st.session_state.get("_persist_last_snapshot") == compact_snapshot:
        return

    backend = _persist_backend()
    if backend == "sqlite":
        if save_sqlite_payload(PERSIST_SQLITE_FILE, payload):
            st.session_state["_persist_last_snapshot"] = compact_snapshot
            return
        # Fallback to JSON if SQLite write fails for any reason.

    try:
        os.makedirs(PERSIST_DIR, exist_ok=True)
        tmp_file = PERSIST_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, PERSIST_FILE)
        st.session_state["_persist_last_snapshot"] = compact_snapshot
    except Exception:
        return


def init_session_state():
    """تجهيز session_state."""
    # Theme: خلفية بيضاء + كروت ناعمة + هيدر GoushFi
    st.markdown(
        """
        <style>
@import url("https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800&family=Plus+Jakarta+Sans:wght@400;600;700;800&display=swap");

:root {
    --brand-1: #0f5f8c;
    --brand-2: #12956b;
    --page-bg-top: #f8fbff;
    --page-bg-bottom: #f2f7fb;
    --surface: #ffffff;
    --surface-muted: #f8fafc;
    --surface-tint: #f3f9fd;
    --text-main: #0f172a;
    --text-soft: #475569;
    --line: #e2e8f0;
    --radius-lg: 16px;
    --radius-md: 12px;
    --shadow-soft: 0 10px 26px rgba(15, 23, 42, 0.08);
}

html, body, [class*="css"] {
    font-family: "Tajawal", "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.stApp {
    background: linear-gradient(180deg, var(--page-bg-top) 0%, var(--page-bg-bottom) 100%) !important;
    color: var(--text-main);
    max-width: 100vw !important;
    overflow-x: hidden !important;
    box-sizing: border-box !important;
}

[data-testid="stAppViewContainer"] {
    overflow-x: hidden !important;
    max-width: 100vw !important;
    width: 100% !important;
    box-sizing: border-box !important;
}

section[data-testid="stMain"] {
    min-width: 0 !important;
    flex: 1 1 auto !important;
    max-width: 100% !important;
    overflow-x: hidden !important;
    box-sizing: border-box !important;
}

header[data-testid="stHeader"] {
    height: 0 !important;
    min-height: 0 !important;
    background: transparent !important;
}

header[data-testid="stHeader"] * {
    display: none !important;
}

#stDecoration {
    display: none !important;
}

/* Hosted + local: trim Streamlit default top padding on the main column */
section[data-testid="stMain"] > div {
    padding-top: 0 !important;
}

div[data-testid="stElementContainer"]:has(div[data-testid="stMarkdownContainer"] > style),
div[data-testid="stElementContainer"]:has(div[data-testid="stMarkdownContainer"] > script),
div[data-testid="stElementContainer"]:has(iframe[height="0"]) {
    position: absolute !important;
    width: 0 !important;
    height: 0 !important;
    min-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
}

.main .block-container {
    max-width: min(1180px, 100%) !important;
    width: 100% !important;
    box-sizing: border-box !important;
    padding-top: 0 !important;
    padding-bottom: 1.8rem !important;
}

h1, h2, h3, [data-testid="stMarkdownContainer"] h1, [data-testid="stMarkdownContainer"] h2, [data-testid="stMarkdownContainer"] h3 {
    font-family: "Plus Jakarta Sans", "Tajawal", sans-serif;
    letter-spacing: -0.01em;
    color: var(--text-main);
}

[data-testid="stSidebar"] {
    background: linear-gradient(180deg, var(--brand-1), var(--brand-2));
    padding-top: 24px;
    border-right: 1px solid rgba(255,255,255,0.14);
    min-width: 16rem !important;
    width: 16rem !important;
    max-width: min(16rem, 88vw) !important;
    box-sizing: border-box !important;
}

[data-testid="stSidebar"] * {
    color: #ffffff !important;
    font-weight: 600;
}

[data-testid="stSidebar"] .stRadio > label {
    background: rgba(255,255,255,0.10);
    border: 1px solid rgba(255,255,255,0.16);
    border-radius: 10px;
    padding: 6px 8px;
}

.flossy-header {
    width: 100%;
    padding: 10px 18px;
    margin-top: 0;
    border-radius: 0 0 var(--radius-lg) var(--radius-lg);
    background: linear-gradient(90deg, var(--brand-1), var(--brand-2));
    color: #f8fafc;
    font-size: 24px;
    font-weight: 800;
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 82px;
    box-shadow: var(--shadow-soft);
}

.flossy-header-inner {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
}

.flossy-header-title {
    display: flex;
    flex-direction: column;
    line-height: 1.06;
    flex: 1 1 auto;
    min-width: 0;
}

.flossy-header-logo-wrap {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 84px;
    height: 84px;
    flex: 0 0 84px;
    max-width: 84px;
    max-height: 84px;
    overflow: hidden;
    box-sizing: border-box;
    border-radius: 12px;
    background: transparent;
}

.flossy-header-inner > div:last-child {
    flex: 0 0 auto;
}

.flossy-header-tagline {
    font-size: 0.8rem;
    font-weight: 600;
    opacity: 0.88;
    margin-top: 5px;
    line-height: 1.2;
}

/* Built-in logo: true colors (no blend filters on the image) */
.flossy-header img.flossy-header-logo,
.flossy-header img {
    height: 100% !important;
    width: 100% !important;
    max-height: 84px !important;
    max-width: 84px !important;
    object-fit: contain !important;
    object-position: center !important;
    border-radius: 10px !important;
    flex-shrink: 0;
    display: block;
}

div[data-testid="stMetric"] {
    background: linear-gradient(135deg, var(--brand-1) 0%, var(--brand-2) 100%);
    padding: 14px 16px 12px 16px;
    border-radius: var(--radius-lg);
    border: 1px solid rgba(255,255,255,0.18);
    box-shadow: var(--shadow-soft);
}

div[data-testid="stMetric"] * {
    color: #ffffff !important;
}

div[data-testid="stMetricLabel"] > div {
    font-size: 0.86rem !important;
    opacity: 0.92;
}

div[data-testid="stMetricValue"] > div {
    font-size: 1.28rem !important;
    font-weight: 800;
}

.stTextInput input,
.stTextArea textarea,
.stNumberInput input,
.stDateInput input,
.stSelectbox [data-baseweb="select"] > div,
.stMultiSelect [data-baseweb="select"] > div {
    border-radius: var(--radius-md) !important;
    border: 1px solid var(--line) !important;
    background: var(--surface) !important;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}

label[data-testid="stWidgetLabel"] p,
div[data-testid="stFileUploader"] small,
.stCaption,
[data-testid="stMarkdownContainer"] p {
    color: var(--text-soft);
}

.stTextInput input:focus,
.stTextArea textarea:focus,
.stNumberInput input:focus {
    border-color: #5fa3c8 !important;
    box-shadow: 0 0 0 3px rgba(95, 163, 200, 0.18) !important;
}

div[data-testid="InputInstructions"] {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    min-height: 0 !important;
    overflow: hidden !important;
}

.stForm {
    border: none !important;
}

div[data-testid="stForm"] {
    background: linear-gradient(180deg, #ffffff 0%, var(--surface-tint) 100%) !important;
    border: 1px solid rgba(15, 95, 140, 0.12) !important;
    border-top: 4px solid var(--brand-2) !important;
    border-radius: 18px !important;
    box-shadow: 0 16px 40px rgba(15, 23, 42, 0.08) !important;
    padding: 18px 16px 14px 16px !important;
    margin: 0.45rem 0 0.9rem 0 !important;
}

.stButton button,
.stDownloadButton button,
.stForm [data-testid="stFormSubmitButton"] button {
    border-radius: 12px !important;
    border: 1px solid #d6e2ec !important;
    background: #ffffff !important;
    color: #0f172a !important;
    font-weight: 700 !important;
    transition: transform 0.12s ease, box-shadow 0.12s ease;
}

.stForm [data-testid="stFormSubmitButton"] button {
    background: linear-gradient(135deg, var(--brand-1), var(--brand-2)) !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
    border: 0 !important;
    box-shadow: 0 10px 24px rgba(15, 95, 140, 0.18) !important;
}

.stButton button[kind="primary"] {
    background: linear-gradient(135deg, var(--brand-1), var(--brand-2)) !important;
    color: #ffffff !important;
    -webkit-text-fill-color: #ffffff !important;
    border: 0 !important;
    box-shadow: 0 10px 24px rgba(15, 95, 140, 0.18) !important;
}

.stForm [data-testid="stFormSubmitButton"] button:disabled,
.stButton button[kind="primary"]:disabled {
    color: rgba(255, 255, 255, 0.96) !important;
    -webkit-text-fill-color: rgba(255, 255, 255, 0.96) !important;
    opacity: 1 !important;
    filter: saturate(0.78) brightness(0.96) !important;
    cursor: not-allowed !important;
}

.stButton button:hover,
.stDownloadButton button:hover,
.stForm [data-testid="stFormSubmitButton"] button:hover {
    transform: translateY(-1px);
    box-shadow: 0 8px 18px rgba(15, 23, 42, 0.08);
}

div[data-testid="stExpander"] {
    border: 1px solid rgba(15, 95, 140, 0.12) !important;
    border-radius: 18px !important;
    background: linear-gradient(180deg, #ffffff 0%, var(--surface-tint) 100%) !important;
    box-shadow: 0 12px 32px rgba(15, 23, 42, 0.06);
    overflow: hidden !important;
}

div[data-testid="stExpander"] details {
    background: transparent !important;
}

div[data-testid="stExpander"] details summary {
    font-weight: 800;
    padding: 0.9rem 1rem !important;
    background: linear-gradient(90deg, rgba(15, 95, 140, 0.08), rgba(18, 149, 107, 0.08));
    border-bottom: 1px solid rgba(15, 95, 140, 0.08);
}

div[data-testid="stExpander"] details > div {
    padding: 0.35rem 0.35rem 0.15rem 0.35rem;
}

div[data-testid="stAlert"] {
    border-radius: 16px !important;
    border: 1px solid rgba(15, 95, 140, 0.1) !important;
    box-shadow: 0 10px 28px rgba(15, 23, 42, 0.05);
}

div[data-testid="stDataFrame"], div[data-testid="stDataEditor"] {
    border: 1px solid var(--line);
    border-radius: var(--radius-md);
    overflow: hidden;
    background: var(--surface);
}

div[data-testid="stTabs"] button[role="tab"] {
    font-weight: 700;
    border-radius: 12px;
    padding: 0.55rem 0.9rem;
    background: rgba(255,255,255,0.72);
    border: 1px solid transparent;
}

div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    background: linear-gradient(135deg, rgba(15, 95, 140, 0.12), rgba(18, 149, 107, 0.12));
    color: var(--brand-1) !important;
    border-color: rgba(15, 95, 140, 0.14);
}

div[data-testid="stFileUploaderDropzone"] {
    border: 1.5px dashed rgba(15, 95, 140, 0.22) !important;
    border-radius: 16px !important;
    background: linear-gradient(180deg, #ffffff 0%, #f5fbff 100%) !important;
}

div[data-testid="stFileUploaderDropzone"]:hover {
    border-color: rgba(18, 149, 107, 0.42) !important;
    background: linear-gradient(180deg, #ffffff 0%, #eef9f4 100%) !important;
}

div[data-testid="stDialog"] {
    align-items: flex-start !important;
    padding-top: 4vh !important;
    padding-bottom: 4vh !important;
}

div[data-testid="stDialog"] div[role="dialog"] {
    border-radius: 20px !important;
    border: 1px solid rgba(15, 95, 140, 0.12) !important;
    background: linear-gradient(180deg, #ffffff 0%, var(--surface-tint) 100%) !important;
    box-shadow: 0 24px 70px rgba(15, 23, 42, 0.18) !important;
    width: min(720px, 92vw) !important;
    max-height: min(88vh, 760px) !important;
    overflow-y: auto !important;
    overscroll-behavior: contain !important;
    -webkit-overflow-scrolling: touch !important;
    margin: 0 auto !important;
}

hr {
    border-color: var(--line) !important;
    margin: 0.65rem 0 !important;
}

.flossy-fab {
    position: fixed;
    right: 18px;
    bottom: 18px;
    width: 56px;
    height: 56px;
    border-radius: 50%;
    background: linear-gradient(90deg, var(--brand-1), var(--brand-2));
    color: #ffffff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 30px;
    font-weight: 800;
    text-decoration: none;
    box-shadow: 0 12px 24px rgba(0,0,0,0.18);
    z-index: 9999;
}

.flossy-fab:hover {
    filter: brightness(1.05);
}

.flossy-fab-label {
    position: fixed;
    right: 82px;
    bottom: 30px;
    background: rgba(255,255,255,0.97);
    border: 1px solid var(--line);
    padding: 6px 10px;
    border-radius: 10px;
    font-size: 13px;
    color: var(--text-main);
    box-shadow: 0 6px 16px rgba(0,0,0,0.10);
    z-index: 9999;
}

@media (max-width: 1100px) {
    .main .block-container {
        max-width: 100% !important;
        padding-left: 0.9rem !important;
        padding-right: 0.9rem !important;
        padding-top: 0 !important;
    }

    [data-testid="stSidebar"] {
        min-width: 15rem !important;
        width: 15rem !important;
        max-width: 15rem !important;
    }

    [data-testid="stSidebar"] .stRadio > label {
        font-size: 0.94rem;
        padding: 6px 7px;
    }

    .flossy-header-inner {
        flex-wrap: wrap;
        align-items: flex-start;
    }

    .flossy-header-title {
        max-width: 100%;
        overflow-wrap: anywhere;
    }

    div[data-testid="stHorizontalBlock"] {
        flex-wrap: wrap !important;
        gap: 0.75rem !important;
    }

    div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
        min-width: 100% !important;
        width: 100% !important;
        max-width: 100% !important;
        flex: 1 1 100% !important;
    }

    div[data-testid="stDataFrame"],
    div[data-testid="stDataEditor"] {
        overflow-x: auto !important;
    }

    div[data-testid="stMetricValue"] > div {
        font-size: 1.14rem !important;
    }

    div[data-testid="stTabs"] button[role="tab"] {
        white-space: normal !important;
        min-height: 44px;
    }
}

@media (max-width: 768px) {
    .main .block-container {
        padding-left: 0.8rem !important;
        padding-right: 0.8rem !important;
        padding-top: 0.12rem !important;
        padding-bottom: 1.2rem !important;
    }

    .flossy-header {
        min-height: 74px;
        font-size: 21px;
        padding: 9px 13px;
        margin-top: 0;
    }

    .flossy-header-inner {
        gap: 10px;
    }

    .flossy-header img.flossy-header-logo,
    .flossy-header img {
        max-height: 68px !important;
        max-width: 68px !important;
    }

    .flossy-header-logo-wrap {
        width: 68px;
        height: 68px;
        flex: 0 0 68px;
        max-width: 68px;
        max-height: 68px;
    }

    [data-testid="stSidebar"] {
        min-width: 13rem !important;
        width: 13rem !important;
        max-width: 13rem !important;
    }

    div[data-testid="stMetric"] {
        padding: 12px 14px 10px 14px;
    }

    div[data-testid="stMetricValue"] > div {
        font-size: 1.08rem !important;
    }
}
        </style>
        """,
        unsafe_allow_html=True,
    )
    if not st.session_state.get("_persist_loaded", False):
        load_persistent_state()
        st.session_state["_persist_loaded"] = True

    settings_loaded_from_persistence = isinstance(st.session_state.get("settings"), dict)

    if not isinstance(st.session_state.get("settings"), dict):
        st.session_state.settings = default_settings.copy()
    else:
        if settings_loaded_from_persistence and "language_user_selected" not in st.session_state.settings:
            st.session_state.settings["language_user_selected"] = True
        for k, v in default_settings.items():
            st.session_state.settings.setdefault(k, v)

    _apply_browser_query_preferences()
    _apply_browser_language_preference()
    _apply_language_direction_theme()

    if not isinstance(st.session_state.get("transactions"), dict):
        st.session_state.transactions = {}

    if not isinstance(st.session_state.get("savings"), dict):
        st.session_state.savings = {}

    if not isinstance(st.session_state.get("project_data"), dict):
        st.session_state.project_data = {}

    if not isinstance(st.session_state.get("recurring"), dict):
        st.session_state.recurring = {"items": []}
    if not isinstance(st.session_state.recurring.get("items"), list):
        st.session_state.recurring["items"] = []

    # المستندات: مفتاح موحد + توافق مع المفتاح القديم
    if not isinstance(st.session_state.get("documents"), list):
        st.session_state["documents"] = []
    legacy_docs = st.session_state.get("mustndaty_documents")
    if isinstance(legacy_docs, list) and not st.session_state.documents:
        st.session_state.documents = legacy_docs
    st.session_state["mustndaty_documents"] = st.session_state.documents

    st.session_state["plan_info"] = _normalize_plan_info(st.session_state.get("plan_info", {}))

    if not isinstance(st.session_state.get("invoices"), list):
        st.session_state["invoices"] = []

    if not isinstance(st.session_state.get("tax_profile"), dict):
        st.session_state["tax_profile"] = {}

    if not isinstance(st.session_state.get("tax_tags"), list):
        st.session_state["tax_tags"] = []

    if not isinstance(st.session_state.get("app_scope"), dict):
        st.session_state["app_scope"] = {"owner_user_id": "", "owner_email": ""}
    st.session_state["app_scope"].setdefault("owner_user_id", "")
    st.session_state["app_scope"].setdefault("owner_email", "")

    st.session_state.setdefault("cloud_auth", {})
    if not isinstance(st.session_state.get("cloud_auth"), dict):
        st.session_state["cloud_auth"] = {}

    st.session_state["cloud_auth"].setdefault("logged_in", False)
    st.session_state["cloud_auth"].setdefault("email", "")
    st.session_state["cloud_auth"].setdefault("user_id", "")
    st.session_state["cloud_auth"].setdefault("access_token", "")
    st.session_state["cloud_auth"].setdefault("refresh_token", "")
    if not bool(st.session_state["cloud_auth"].get("logged_in")) or not str(st.session_state["cloud_auth"].get("access_token") or "").strip():
        st.session_state["_cloud_cookie_restore_checked"] = False
    st.session_state.setdefault("_cloud_last_snapshot", "")
    st.session_state.setdefault("_cloud_last_pull_user", "")
    st.session_state.setdefault("_cloud_auth_issued_at", "")
    st.session_state.setdefault("_cloud_sync_last_error", "")

def get_month_selection(page: str):
    """
    ترجع (month_key, month, year)
    تستخدم الـ sidebar لاختيار الشهر/السنة، ما عدا الإعدادات.
    """
    from services.i18n import make_t, get_months, get_lang_code
    t = make_t()
    _lc = get_lang_code()
    _display_months = get_months()

    if page == "settings":
        return None, None, None

    st.sidebar.markdown("---")
    st.sidebar.subheader(t("الشهر المعروض", "Selected Month"))

    now = datetime.now()
    current_year = now.year
    current_month_idx = now.month - 1

    if page in ["account", "savings", "project", "tax"]:
        year_options = list(range(2023, current_year + 3))
        year_default_index = year_options.index(current_year)

        month_options = _display_months
        month_selected = st.sidebar.selectbox(t("الشهر", "Month"), month_options, index=current_month_idx)
        year = st.sidebar.selectbox(t("السنة", "Year"), year_options, index=year_default_index)
        month = arabic_months[_display_months.index(month_selected)] if _lc != "ar" else month_selected
    else:
        month = arabic_months[current_month_idx]
        year = current_year
        month_caption = _display_months[current_month_idx] if _lc != "ar" else month
        st.sidebar.caption(f"{month_caption} {year}")

    month_key = f"{year}-{month}"
    return month_key, month, year


def get_logo_bytes():
    """Return the user-uploaded logo bytes, or None."""
    settings = st.session_state.settings
    if settings.get("profile_image") is not None:
        return settings["profile_image"]

    return None


_BUILTIN_LOGO_DATA_URI: str | None = None


def _builtin_logo_raw_data_uri(raw: bytes) -> str:
    if raw.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif raw.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif raw.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif raw.startswith(b"RIFF") and len(raw) > 12 and raw[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def _builtin_logo_data_uri_with_dark_matte_removed(raw: bytes) -> str | None:
    """Drop near-black letterboxing to transparency; keeps logo colors unchanged."""
    try:
        from io import BytesIO

        import numpy as np
        from PIL import Image
    except Exception:
        return None

    try:
        im = Image.open(BytesIO(raw)).convert("RGBA")
        arr = np.asarray(im, dtype=np.uint8)
        r = arr[:, :, 0].astype(np.int16)
        g = arr[:, :, 1].astype(np.int16)
        b = arr[:, :, 2].astype(np.int16)
        mx = np.maximum(np.maximum(r, g), b)
        mn = np.minimum(np.minimum(r, g), b)
        chroma = mx - mn
        # Black / dark-neutral letterbox only; keep saturated logo colors
        dark_neutral = (chroma < 20) & (mx < 52)
        arr = arr.copy()
        arr[:, :, 3] = np.where(dark_neutral, np.uint8(0), arr[:, :, 3])
        out = Image.fromarray(arr, mode="RGBA")
        buf = BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"
    except Exception:
        return None


def get_builtin_logo_b64() -> str:
    """Return the built-in GoushFi logo as a base64 data URI (cached)."""
    global _BUILTIN_LOGO_DATA_URI
    if _BUILTIN_LOGO_DATA_URI is None:
        logo_path = os.path.join(os.path.dirname(__file__), "goushfi_logo.png")
        with open(logo_path, "rb") as f:
            raw = f.read()
        processed = _builtin_logo_data_uri_with_dark_matte_removed(raw)
        _BUILTIN_LOGO_DATA_URI = processed if processed else _builtin_logo_raw_data_uri(raw)
    return _BUILTIN_LOGO_DATA_URI


def ensure_month_keys(month_key: str):
    """نضمن إن الدكتات فيها مفاتيح الشهر الحالي."""
    if month_key is None:
        return

    if month_key not in st.session_state.transactions:
        st.session_state.transactions[month_key] = []

    if month_key not in st.session_state.savings:
        st.session_state.savings[month_key] = {
            "goal": 0.0,
            "transactions": [],
        }

    if month_key not in st.session_state.project_data:
        st.session_state.project_data[month_key] = {
            "project_name": "",
            "licenses": [],
            "budget_expected_income": 0.0,
            "budget_expected_operating": 0.0,
            "budget_note": "",
            "project_transactions": [],
            "assets": [],
            "projects": {},
            "selected_project": "",
        }


def load_transactions(month_key: str):
    """تحميل معاملات شهر محدد من التخزين (session_state)."""
    ensure_month_keys(month_key)
    tx_list = st.session_state.transactions[month_key]
    changed = False
    for idx, tx in enumerate(tx_list):
        if not isinstance(tx, dict):
            continue
        normalized = ExpenseTaxService.normalize_transaction(st.session_state, tx)
        if normalized != tx:
            tx_list[idx] = normalized
            changed = True
    if changed:
        st.session_state.transactions[month_key] = tx_list
    return st.session_state.transactions[month_key]


def add_transaction(month_key: str, tx: dict):
    """إضافة معاملة جديدة لشهر محدد في التخزين (session_state)."""
    ensure_month_keys(month_key)
    tx_payload = dict(tx) if isinstance(tx, dict) else {}
    tx_payload = ExpenseTaxService.normalize_transaction(st.session_state, tx_payload)
    st.session_state.transactions[month_key].append(tx_payload)


def get_all_transactions_df(currency: str) -> pd.DataFrame:
    """كل المعاملات من كل الشهور كـ DataFrame مفلتر بالعملة."""
    all_tx = []
    for _mk, lst in st.session_state.transactions.items():
        all_tx.extend(lst)

    if not all_tx:
        return pd.DataFrame()

    df_all = pd.DataFrame(all_tx)
    df_all_curr = df_all[df_all["currency"].apply(lambda value: currency_matches(value, currency))].copy()
    if df_all_curr.empty:
        return pd.DataFrame()

    df_all_curr["date_dt"] = pd.to_datetime(df_all_curr["date"])
    df_all_curr["year_month"] = df_all_curr["date_dt"].dt.strftime("%Y-%m")
    return df_all_curr


def get_saving_totals():
    """إجمالي التوفير (كل الشهور)."""
    total_in = 0.0
    total_out = 0.0
    for s in st.session_state.savings.values():
        txs = s.get("transactions", [])
        total_in += sum(t["amount"] for t in txs if t["type"] == "إيداع")
        total_out += sum(t["amount"] for t in txs if t["type"] == "سحب")
    return total_in, total_out

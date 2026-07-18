from __future__ import annotations

import html
from datetime import date

import pandas as pd
import streamlit as st

from config_floosy import arabic_months, english_months, load_transactions
from services.currency_localization import currency_short_label
from services.i18n import dashboard_brief_copy, format_i18n, make_t, get_lang_code, get_months
from repositories.session_repo import SessionStateRepository
from services.cash_flow_engine import CashFlowEngine
from services.financial_analyzer import FinancialAnalyzer
from services.financial_calm_brief import FinancialCalmBriefEngine
from services.ai_insights_service import AIInsightsService
from services.build_week_mode import should_show_financial_calm_brief
from services.transaction_categories import CATEGORY_AR_TO_EN, CATEGORY_EN_TO_AR, category_label


CALM_UI_EN = {
    "title": "Financial Calm Brief",
    "subtitle": "Only three decisions. Python calculates the numbers; AI explains why without changing them.",
    "button": "Explain Decisions with AI",
    "spinner": "Explaining decisions...",
    "fallback": "Using the safe deterministic explanation. Add OPENAI_API_KEY for optional AI explanation.",
    "stale": "Data changed; the updated deterministic explanation is shown.",
    "rejected": "The AI response did not pass validation; the safe deterministic explanation is being used.",
}


def _previous_month_key(current_month: str, current_year: int) -> str:
    month_idx = arabic_months.index(current_month)
    if month_idx == 0:
        return f"{current_year - 1}-{arabic_months[-1]}"
    return f"{current_year}-{arabic_months[month_idx - 1]}"


def _request_user_agent() -> str:
    try:
        context = getattr(st, "context", None)
        headers = getattr(context, "headers", {}) if context is not None else {}
    except Exception:
        return ""

    if hasattr(headers, "get"):
        for header_name in ("user-agent", "User-Agent", "USER-AGENT"):
            try:
                value = headers.get(header_name)
            except Exception:
                value = ""
            if value:
                return str(value).strip()
    return ""


def _section_label(title: str, summary: str = "") -> str:
    return f"{title} | {summary}" if summary else title


def _quick_take_theme(status: str) -> dict:
    if status == "empty":
        return {
            "background": "#FFFFFF",
            "border": "#E2E8F0",
            "label": "#64748B",
            "text": "#0F172A",
            "pill_bg": "#F8FAFC",
            "pill_border": "#E2E8F0",
            "pill_text": "#475569",
        }
    if status in {"cash_pressure_90", "coverage_gap", "project_pressure"}:
        return {
            "background": "#FFF8F6",
            "border": "#E8B4A3",
            "label": "#8A4A3C",
            "text": "#6F352C",
            "pill_bg": "#FFFFFF",
            "pill_border": "#F1D1C4",
            "pill_text": "#8A4A3C",
        }
    if status in {"needs_follow_up", "spending_high", "docs_due", "note_pattern"}:
        return {
            "background": "#FFFDF5",
            "border": "#F6C453",
            "label": "#92400E",
            "text": "#78350F",
            "pill_bg": "#FFFFFF",
            "pill_border": "#F9E7A7",
            "pill_text": "#92400E",
        }
    return {
        "background": "#EAF5EF",
        "border": "#047857",
        "label": "#065F46",
        "text": "#064E3B",
        "pill_bg": "#F6FBF8",
        "pill_border": "#BFD8CC",
        "pill_text": "#064E3B",
    }


def _card_colors(value: float) -> dict:
    if value > 0:
        return {"bg": "#EAF5EF", "border": "#047857", "label": "#065F46", "value": "#064E3B"}
    if value < 0:
        return {"bg": "#FFF8F6", "border": "#E8B4A3", "label": "#8A4A3C", "value": "#6F352C"}
    return {"bg": "#F8FAFC", "border": "#CBD5E1", "label": "#64748B", "value": "#334155"}


def _render_analyzer_polish_css(is_ltr: bool) -> None:
    text_align = "left" if is_ltr else "right"
    st.markdown(
        f"""
        <style>
        section[data-testid="stMain"] .stMainBlockContainer.block-container,
        section[data-testid="stMain"] .block-container {{
            max-width: min(1180px, 100%) !important;
            padding-top: 0.25rem !important;
            padding-left: clamp(0.95rem, 2vw, 1.9rem) !important;
            padding-right: clamp(0.95rem, 2vw, 1.9rem) !important;
        }}
        section[data-testid="stMain"] div[data-testid="stVerticalBlock"] {{
            gap: 0.78rem;
        }}
        section[data-testid="stMain"] h1 {{
            margin-bottom: 0.12rem !important;
            font-size: 2.05rem !important;
            line-height: 1.08 !important;
            letter-spacing: 0 !important;
        }}
        section[data-testid="stMain"] h3 {{
            margin-top: 0.4rem !important;
            margin-bottom: 0.28rem !important;
            font-size: 1.32rem !important;
            line-height: 1.2 !important;
            letter-spacing: 0 !important;
        }}
        section[data-testid="stMain"] h4 {{
            margin-top: 0.35rem !important;
            margin-bottom: 0.28rem !important;
            line-height: 1.25 !important;
            letter-spacing: 0 !important;
        }}
        section[data-testid="stMain"] p,
        section[data-testid="stMain"] .stCaption,
        section[data-testid="stMain"] [data-testid="stCaptionContainer"] {{
            line-height: 1.48;
        }}
        .floosy-analyzer-action-card {{
            min-height: 138px;
            border-radius: 16px;
            padding: 15px 16px 14px 16px;
            box-shadow: 0 12px 28px rgba(15, 23, 42, 0.075);
            position: relative;
            overflow: hidden;
        }}
        .floosy-analyzer-action-card::before {{
            content: "";
            position: absolute;
            inset: 0;
            background: linear-gradient(135deg, rgba(255,255,255,0.78), rgba(255,255,255,0.28));
            pointer-events: none;
        }}
        .floosy-analyzer-action-card__content {{
            position: relative;
            z-index: 1;
        }}
        .floosy-analyzer-action-card__label {{
            font-size: 0.82rem;
            font-weight: 800;
            line-height: 1.25;
            margin-bottom: 0.35rem;
        }}
        .floosy-analyzer-action-card__value {{
            font-size: clamp(1.24rem, 2.6vw, 1.52rem);
            font-weight: 850;
            line-height: 1.15;
            overflow-wrap: anywhere;
        }}
        .floosy-analyzer-action-card__delta {{
            font-size: 0.78rem;
            line-height: 1.38;
            margin-top: 0.45rem;
            overflow-wrap: anywhere;
        }}
        section[data-testid="stMain"] div[data-testid="stMetric"] {{
            border: 1px solid rgba(15, 23, 42, 0.085);
            border-radius: 13px;
            background: rgba(255, 255, 255, 0.72);
            padding: 0.78rem 0.82rem;
            min-height: 104px;
            box-shadow: 0 8px 20px rgba(15, 23, 42, 0.035);
        }}
        section[data-testid="stMain"] div[data-testid="stMetricLabel"] p {{
            font-size: 0.78rem !important;
            line-height: 1.24 !important;
            color: #64748B !important;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
            text-align: {text_align} !important;
        }}
        section[data-testid="stMain"] div[data-testid="stMetricValue"] {{
            font-size: 1.05rem !important;
            line-height: 1.2 !important;
            color: #0F172A !important;
            overflow-wrap: anywhere !important;
            text-align: {text_align} !important;
        }}
        section[data-testid="stMain"] div[data-testid="stMetricDelta"] p {{
            font-size: 0.73rem !important;
            line-height: 1.28 !important;
            white-space: normal !important;
            overflow-wrap: anywhere !important;
        }}
        section[data-testid="stMain"] div[data-testid="stAlert"] {{
            border-radius: 14px !important;
            border-color: rgba(15, 95, 140, 0.14) !important;
            background: rgba(248, 250, 252, 0.86) !important;
            box-shadow: 0 8px 20px rgba(15, 23, 42, 0.035) !important;
        }}
        section[data-testid="stMain"] div[data-testid="stAlert"] p {{
            font-size: 0.88rem !important;
            line-height: 1.45 !important;
            color: #334155 !important;
        }}
        section[data-testid="stMain"] div[data-testid="stExpander"] {{
            border: 1px solid rgba(15, 95, 140, 0.10) !important;
            border-radius: 15px !important;
            background: rgba(255, 255, 255, 0.76) !important;
            box-shadow: 0 8px 20px rgba(15, 23, 42, 0.04) !important;
            margin: 0.12rem 0 0.5rem 0 !important;
            overflow: hidden !important;
        }}
        section[data-testid="stMain"] div[data-testid="stExpander"] details summary {{
            min-height: 46px;
            padding: 0.68rem 0.9rem !important;
            background: linear-gradient(90deg, rgba(15, 95, 140, 0.055), rgba(18, 149, 107, 0.055)) !important;
            font-size: 0.92rem !important;
            font-weight: 800 !important;
            line-height: 1.3 !important;
        }}
        section[data-testid="stMain"] div[data-testid="stExpander"] details[open] summary {{
            border-bottom: 1px solid rgba(15, 95, 140, 0.08) !important;
        }}
        section[data-testid="stMain"] div[data-testid="stExpander"] details summary p {{
            white-space: normal !important;
            overflow-wrap: anywhere !important;
        }}
        section[data-testid="stMain"] div[data-testid="stExpander"] details > div {{
            padding: 0.45rem 0.55rem 0.4rem 0.55rem !important;
        }}
        section[data-testid="stMain"] div[data-testid="stDataFrame"] {{
            border-radius: 13px;
            overflow: hidden;
            border: 1px solid rgba(15, 23, 42, 0.08);
        }}
        @media (max-width: 760px) {{
            section[data-testid="stMain"] .stMainBlockContainer.block-container,
            section[data-testid="stMain"] .block-container {{
                padding-left: 0.72rem !important;
                padding-right: 0.72rem !important;
            }}
            section[data-testid="stMain"] h1 {{
                font-size: 1.55rem !important;
            }}
            section[data-testid="stMain"] h3 {{
                font-size: 1.14rem !important;
            }}
            section[data-testid="stMain"] div[data-testid="column"] {{
                flex: 1 1 100% !important;
                width: 100% !important;
                min-width: 100% !important;
            }}
            .floosy-analyzer-action-card {{
                min-height: 116px;
                padding: 13px 14px;
            }}
            .floosy-analyzer-action-card__value {{
                font-size: 1.24rem;
            }}
            section[data-testid="stMain"] div[data-testid="stMetric"] {{
                min-height: 88px;
                padding: 0.7rem 0.74rem;
            }}
            section[data-testid="stMain"] div[data-testid="stExpander"] details summary {{
                padding: 0.64rem 0.72rem !important;
                font-size: 0.86rem !important;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_action_card(title: str, value_text: str, delta_text: str, net_value: float, is_ltr: bool) -> None:
    colors = _card_colors(net_value)
    accent_side = "border-left" if is_ltr else "border-right"
    text_dir = "ltr" if is_ltr else "rtl"
    text_align = "left" if is_ltr else "right"
    st.markdown(
        f"""
        <div class="floosy-analyzer-action-card" style="
          border:1px solid {colors['border']};
          {accent_side}:5px solid {colors['border']};background:linear-gradient(135deg, {colors['bg']}, #FFFFFF);
          direction:{text_dir};text-align:{text_align};
        ">
          <div class="floosy-analyzer-action-card__content">
            <div class="floosy-analyzer-action-card__label" style="color:{colors['label']};">{title}</div>
            <div class="floosy-analyzer-action-card__value" style="color:{colors['value']};">{value_text}</div>
            <div class="floosy-analyzer-action-card__delta" style="color:{colors['label']};">{delta_text}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _build_ai_cash_flow_context(
    *,
    month_key: str,
    currency_view: str,
    current: dict,
    comparison: dict,
    coverage: dict,
    cash_flow: dict,
    savings: dict,
    project: dict,
    docs: dict,
    seasonal: dict,
    category_signal: dict,
    brief: dict,
) -> dict:
    return {
        "month_key": month_key,
        "currency": currency_view,
        "current_month": current,
        "month_comparison": comparison,
        "recurring_coverage": coverage,
        "cash_flow_90d": {
            "as_of": cash_flow.get("as_of"),
            "forecast_until": cash_flow.get("forecast_until"),
            "actual_last_90": cash_flow.get("actual_last_90", {}),
            "projected_next_90": cash_flow.get("projected_next_90", {}),
            "components": cash_flow.get("components", {}),
            "comparison_vs_last_90": cash_flow.get("comparison_vs_last_90", {}),
            "carry_over": cash_flow.get("carry_over", {}),
            "monthly_projection": cash_flow.get("monthly_projection", [])[:6],
            "upcoming_items": cash_flow.get("upcoming_items", [])[:8],
        },
        "savings": savings,
        "projects": project,
        "documents": docs,
        "seasonal_expense": seasonal,
        "category_signal": category_signal,
        "deterministic_brief": brief,
    }


def _render_ai_result_list(title: str, rows: list[str]) -> None:
    if not rows:
        return
    st.markdown(f"**{title}**")
    for row in rows[:5]:
        st.write(f"- {row}")


def _render_ai_cash_flow_result(result: dict, t) -> None:
    if not result:
        return

    if not result.get("ok"):
        st.warning(
            t(
                "تعذر توليد التقرير الذكي حاليًا.",
                "Could not generate the AI brief right now.",
            )
        )
        if result.get("error"):
            st.caption(str(result.get("error")))
        return

    summary = str(result.get("summary") or "").strip()
    if summary:
        st.info(summary)

    c1, c2 = st.columns(2)
    with c1:
        _render_ai_result_list(t("المخاطر", "Risks"), result.get("risks", []))
        _render_ai_result_list(t("الفرص", "Opportunities"), result.get("opportunities", []))
    with c2:
        _render_ai_result_list(t("الخطوات التالية", "Next Actions"), result.get("next_actions", []))
        _render_ai_result_list(t("نواقص البيانات", "Data Gaps"), result.get("data_gaps", []))

    confidence = str(result.get("confidence") or "low").strip()
    confidence_label = {
        "low": t("منخفضة", "Low"),
        "medium": t("متوسطة", "Medium"),
        "high": t("عالية", "High"),
    }.get(confidence, confidence)
    st.caption(t(f"ثقة التحليل: {confidence_label}", f"Analysis confidence: {confidence_label}"))


def _render_calm_decision_card(
    decision: dict,
    *,
    explanation: str,
    currency_view: str,
    is_en: bool,
    is_ltr: bool,
) -> None:
    metric = decision.get("metric", {})
    metric_value = float(metric.get("value", 0.0) or 0.0)
    if metric.get("kind") == "count":
        metric_text = html.escape(f"{int(metric_value):,}")
    else:
        metric_text = html.escape(f"{metric_value:,.2f} {currency_view}")

    lang_key = "en" if is_en else "ar"
    title = html.escape(str(decision.get(f"title_{lang_key}") or ""))
    metric_label = html.escape(str(metric.get(f"label_{lang_key}") or ""))
    safe_explanation = html.escape(str(explanation or ""))
    action = html.escape(str(decision.get(f"action_{lang_key}") or ""))
    tone_colors = {
        "risk": {"border": "#C2410C", "bg": "#FFF7ED", "text": "#7C2D12"},
        "attention": {"border": "#D97706", "bg": "#FFFBEB", "text": "#78350F"},
        "calm": {"border": "#047857", "bg": "#ECFDF5", "text": "#064E3B"},
        "neutral": {"border": "#64748B", "bg": "#F8FAFC", "text": "#334155"},
    }
    colors = tone_colors.get(str(decision.get("tone") or "neutral"), tone_colors["neutral"])
    accent_side = "border-left" if is_ltr else "border-right"
    direction = "ltr" if is_ltr else "rtl"
    text_align = "left" if is_ltr else "right"

    st.markdown(
        f"""
        <div style="
          min-height:265px;border:1px solid {colors['border']};{accent_side}:6px solid {colors['border']};
          border-radius:16px;padding:16px;background:linear-gradient(145deg,{colors['bg']},#FFFFFF);
          box-shadow:0 10px 24px rgba(15,23,42,0.06);direction:{direction};text-align:{text_align};
        ">
          <div style="font-size:1.02rem;font-weight:850;color:{colors['text']};line-height:1.35;">{title}</div>
          <div style="margin-top:13px;font-size:1.45rem;font-weight:900;color:{colors['text']};">{metric_text}</div>
          <div style="font-size:0.78rem;color:#64748B;margin-top:2px;">{metric_label}</div>
          <div style="font-size:0.88rem;color:#334155;line-height:1.5;margin-top:13px;">{safe_explanation}</div>
          <div style="font-size:0.82rem;color:{colors['text']};line-height:1.45;margin-top:13px;padding-top:10px;border-top:1px solid rgba(100,116,139,0.18);">
            {action}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render(month_key: str, month: str, year: int):
    _lc = get_lang_code()
    is_en = _lc == "en"
    is_ltr = _lc != "ar"
    t = make_t()
    _display_months = get_months()
    month_display = _display_months[arabic_months.index(month)] if (_lc != "ar" and month in arabic_months) else month

    _render_analyzer_polish_css(is_ltr)

    st.title(t("المحلل المالي", "Financial Analyzer"))
    st.caption(format_i18n("analyzer_month_caption", month=month_display, year=year))

    currency = st.session_state.settings.get("default_currency", "د.ك")
    currency_view = currency_short_label(currency, _lc)

    repo = SessionStateRepository()
    analyzer = FinancialAnalyzer(repo)
    cash_flow_engine = CashFlowEngine(repo)
    calm_brief_engine = FinancialCalmBriefEngine()

    current_tx = load_transactions(month_key)
    previous_month_key = _previous_month_key(month, year)
    previous_tx = load_transactions(previous_month_key)

    current = analyzer.totals_by_currency(current_tx, currency)
    previous = analyzer.totals_by_currency(previous_tx, currency)
    comparison = analyzer.compare_totals(current, previous)

    recurring_items = st.session_state.get("recurring", {}).get("items", [])
    active_items = [item for item in recurring_items if item.get("active", True)]
    coverage = analyzer.recurring_coverage(active_items, month_key, currency)

    projected_net_after_pending = current["net"] + coverage["net_coverage"]

    cash_flow = cash_flow_engine.cash_flow_90d(
        st.session_state,
        currency,
        as_of=date.today(),
        horizon_days=90,
    )
    actual_90 = cash_flow["actual_last_90"]
    projected_90 = cash_flow["projected_next_90"]
    carry_over = cash_flow["carry_over"]
    comparison_90 = cash_flow["comparison_vs_last_90"]
    components_90 = cash_flow["components"]

    savings = analyzer.savings_summary(st.session_state, month_key)
    project = analyzer.projects_summary(st.session_state, month_key)
    docs = analyzer.documents_summary(st.session_state)
    project_impact = analyzer.project_impact_on_personal(st.session_state, month_key, currency)
    seasonal = analyzer.seasonal_expense_summary(st.session_state, currency, limit_months=6)
    category_signal = analyzer.seasonal_category_signal(st.session_state, month_key, currency, history_months=6)
    category_value = str(category_signal.get("top_category") or "").strip()
    category_ar = CATEGORY_EN_TO_AR.get(category_value, category_value)
    category_signal_for_calm = {
        **category_signal,
        "top_category_ar": category_ar,
        "top_category_en": CATEGORY_AR_TO_EN.get(category_ar, category_value),
    }

    # ── Zone A: AI Quick Take ─────────────────────────────────────────────
    brief = analyzer.dashboard_brief(st.session_state, month_key, currency)
    brief_status = str(brief.get("status", "stable") or "stable")

    show_spending_note_on_good = brief_status == "spending_high" and float(brief.get("focus_value", 0.0)) >= 0
    theme = _quick_take_theme("stable" if show_spending_note_on_good else brief_status)
    border_side = "border-left" if is_ltr else "border-right"

    brief_message, brief_detail, focus_label, support_label = dashboard_brief_copy(
        brief,
        currency_view,
        spending_note_on_good=show_spending_note_on_good,
    )
    focus_value = f"{brief.get('focus_value', 0.0):,.0f} {currency_view}"
    support_value = f"{brief.get('support_value', 0.0):,.0f} {currency_view}"

    st.markdown(
        f"### {t('نظرة سريعة', 'Quick Take')}",
    )

    if brief_status == "empty":
        st.info(
            t(
                "أضف أول حركة مالية لتفعيل التحليل.",
                "Add your first transaction to activate the analysis.",
            )
        )
    else:
        st.markdown(
            f"""
            <div style="background:{theme['background']};border:1px solid {theme['pill_border']};{border_side}:6px solid {theme['border']};border-radius:12px;padding:12px 14px;margin-bottom:8px;">
                <div style="font-weight:700;font-size:1.08rem;color:{theme['text']};">{brief_message}</div>
                <div style="color:{theme['label']};font-size:0.92rem;margin-top:4px;">{brief_detail}</div>
                <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;">
                    <div style="background:{theme['pill_bg']};border:1px solid {theme['pill_border']};border-radius:999px;padding:6px 10px;font-size:0.85rem;">
                        <span style="color:{theme['label']};">{focus_label}:</span>
                        <span style="font-weight:700;color:{theme['pill_text']};"> {focus_value}</span>
                    </div>
                    <div style="background:{theme['pill_bg']};border:1px solid {theme['pill_border']};border-radius:999px;padding:6px 10px;font-size:0.85rem;">
                        <span style="color:{theme['label']};">{support_label}:</span>
                        <span style="font-weight:700;color:{theme['pill_text']};"> {support_value}</span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Thin-data calm caption
    tx_by_month = st.session_state.get("transactions", {})
    tx_count = sum(len(txs or []) for txs in tx_by_month.values()) if isinstance(tx_by_month, dict) else 0
    if 0 < tx_count < 5 and seasonal.get("history_month_count", 0) < 2:
        st.caption(
            t(
                "بيانات محدودة — ستتحسن الرؤى بإضافة المزيد من المعاملات.",
                "Limited data — insights will improve as you add more transactions.",
            )
        )

    show_financial_calm_brief = should_show_financial_calm_brief(
        cloud_auth=st.session_state.get("cloud_auth", {}),
        app_scope=st.session_state.get("app_scope", {}),
        query_params=getattr(st, "query_params", {}),
        user_agent=_request_user_agent(),
    )
    ai_service = AIInsightsService.from_runtime(getattr(st, "secrets", None))

    if show_financial_calm_brief:
        calm_brief = calm_brief_engine.build(
            month_key=month_key,
            currency=currency_view,
            current=current,
            comparison=comparison,
            coverage=coverage,
            cash_flow=cash_flow,
            savings=savings,
            seasonal=seasonal,
            category_signal=category_signal_for_calm,
        )
        calm_snapshot = AIInsightsService.context_snapshot(calm_brief)
        stored_ai = st.session_state.get("_financial_calm_ai", {})
        if not isinstance(stored_ai, dict):
            stored_ai = {}

        st.markdown(f"### {t('ملخص الهدوء المالي', CALM_UI_EN['title'])}")
        st.caption(
            t(
                "ثلاثة قرارات فقط. Python يحسب الأرقام، والذكاء الاصطناعي يشرح السبب بدون تغييرها.",
                CALM_UI_EN["subtitle"],
            )
        )

        if ai_service.is_configured:
            if st.button(
                t("اشرح القرارات بالذكاء الاصطناعي", CALM_UI_EN["button"]),
                key="generate_financial_calm_explanations",
                use_container_width=True,
            ):
                with st.spinner(t("جاري شرح القرارات...", CALM_UI_EN["spinner"])):
                    ai_result = ai_service.generate_financial_calm_explanations(
                        calm_brief,
                        language=_lc,
                    )
                st.session_state["_financial_calm_ai"] = {
                    "snapshot": calm_snapshot,
                    "result": ai_result,
                }
                stored_ai = st.session_state["_financial_calm_ai"]
        else:
            st.caption(
                t(
                    "يعمل الآن بالشرح الحتمي الآمن. أضيفي OPENAI_API_KEY لتفعيل الشرح الاختياري.",
                    CALM_UI_EN["fallback"],
                )
            )

        ai_result = stored_ai.get("result", {}) if stored_ai.get("snapshot") == calm_snapshot else {}
        ai_explanations = ai_result.get("explanations", {}) if ai_result.get("ok") else {}
        if stored_ai.get("result") and stored_ai.get("snapshot") != calm_snapshot:
            st.caption(
                t(
                    "تغيرت البيانات؛ يظهر الآن الشرح الحتمي المحدث.",
                    CALM_UI_EN["stale"],
                )
            )
        elif ai_result and not ai_result.get("ok"):
            st.caption(
                t(
                    "لم يجتز رد الذكاء الاصطناعي التحقق؛ تم استخدام الشرح الحتمي الآمن.",
                    CALM_UI_EN["rejected"],
                )
            )

        decision_columns = st.columns(3)
        for column, decision in zip(decision_columns, calm_brief.get("decisions", [])):
            decision_id = str(decision.get("decision_id") or "")
            fallback_key = "fallback_explanation_en" if _lc != "ar" else "fallback_explanation_ar"
            explanation = str(ai_explanations.get(decision_id) or decision.get(fallback_key) or "")
            with column:
                _render_calm_decision_card(
                    decision,
                    explanation=explanation,
                    currency_view=currency_view,
                    is_en=_lc != "ar",
                    is_ltr=is_ltr,
                )
    else:
        ai_context = _build_ai_cash_flow_context(
            month_key=month_key,
            currency_view=currency_view,
            current=current,
            comparison=comparison,
            coverage=coverage,
            cash_flow=cash_flow,
            savings=savings,
            project=project,
            docs=docs,
            seasonal=seasonal,
            category_signal=category_signal,
            brief=brief,
        )
        ai_snapshot = AIInsightsService.context_snapshot(ai_context)
        stored_ai = st.session_state.get("_ai_cash_flow_brief", {})
        if not isinstance(stored_ai, dict):
            stored_ai = {}

        st.markdown(f"### {t('التقرير الذكي', 'AI Brief')}")
        if not ai_service.is_configured:
            st.caption(
                t(
                    "المساعد الذكي غير مفعّل في هذه البيئة. أضف OPENAI_API_KEY لتشغيل التقرير الذكي.",
                    "AI brief is not configured in this environment. Add OPENAI_API_KEY to enable it.",
                )
            )
        elif st.button(
            t("توليد تقرير ذكي", "Generate AI Brief"),
            key="generate_ai_cash_flow_brief",
            use_container_width=True,
        ):
            with st.spinner(t("جاري توليد التقرير...", "Generating brief...")):
                ai_result = ai_service.generate_cash_flow_brief(ai_context, language=_lc)
            st.session_state["_ai_cash_flow_brief"] = {
                "snapshot": ai_snapshot,
                "result": ai_result,
            }
            stored_ai = st.session_state["_ai_cash_flow_brief"]

        if stored_ai.get("snapshot") == ai_snapshot:
            _render_ai_cash_flow_result(stored_ai.get("result", {}), t)
        elif stored_ai.get("result"):
            st.caption(
                t(
                    "تغيرت البيانات منذ آخر تقرير ذكي. ولّد تقريرًا جديدًا للحصول على قراءة محدثة.",
                    "Data changed since the last AI brief. Generate a new brief for an updated read.",
                )
            )

    # ── Zone B: 3 Action Cards ────────────────────────────────────────────
    if brief_status != "empty":
        ac1, ac2, ac3 = st.columns(3)
        with ac1:
            _render_action_card(
                title=t("هذا الشهر", "This Month"),
                value_text=f"{current['net']:,.2f} {currency_view}",
                delta_text=f"{comparison['net_delta']:+,.2f} ({comparison['net_delta_pct']:+.1f}%)",
                net_value=current["net"],
                is_ltr=is_ltr,
            )
        with ac2:
            _render_action_card(
                title=t("الاستحقاقات", "Entitlements"),
                value_text=f"{coverage['net_coverage']:,.2f} {currency_view}",
                delta_text=format_i18n(
                    "entitlement_card_delta",
                    overdue=coverage["overdue_count"],
                    expected=coverage["expected_count"],
                ),
                net_value=coverage["net_coverage"],
                is_ltr=is_ltr,
            )
        with ac3:
            _render_action_card(
                title=t("90 يوم", "90-Day Outlook"),
                value_text=f"{projected_90['net']:,.2f} {currency_view}",
                delta_text=format_i18n(
                    "delta_vs_last_90",
                    delta=comparison_90["net_delta"],
                ),
                net_value=projected_90["net"],
                is_ltr=is_ltr,
            )
        st.markdown("")

    # ── Zone C: Upcoming Items (above the fold) ──────────────────────────
    upcoming_items = cash_flow.get("upcoming_items", [])
    if upcoming_items:
        source_labels = {
            "recurring": t("قالب متكرر", "Recurring"),
            "invoice": t("فاتورة", "Invoice"),
            "document": t("مستند", "Document"),
            "transaction": t("معاملة", "Transaction"),
        }
        type_labels = {
            "income": t("دخل", "Income"),
            "expense": t("مصروف", "Expense"),
        }
        upcoming_df = pd.DataFrame(
            [
                {
                    t("التاريخ", "Date"): item["due_date_iso"],
                    t("المصدر", "Source"): source_labels.get(item.get("source", ""), item.get("source", "")),
                    t("النوع", "Type"): type_labels.get(item.get("type", ""), item.get("type", "")),
                    t("العنصر", "Item"): item.get("name", ""),
                    t("الحالة", "Status"): item.get("status", ""),
                    t("المبلغ", "Amount"): f"{item['amount']:,.2f} {currency_view}",
                }
                for item in upcoming_items
            ]
        )
        st.markdown(f"#### {t('أقرب العناصر القادمة', 'Upcoming Items')}")
        st.dataframe(upcoming_df, hide_index=True)

    # ── Zone D: Detail Expanders (below the fold) ────────────────────────
    st.markdown(f"### {t('التفاصيل', 'Details')}")

    with st.expander(
        _section_label(
            t("ملخص هذا الشهر", "This Month Overview"),
            f"{t('الصافي', 'Net')} {current['net']:,.2f} {currency_view}",
        ),
        expanded=False,
    ):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric(
                t("الدخل", "Income"),
                f"{current['income']:,.2f} {currency_view}",
                delta=f"{comparison['income_delta']:+,.2f} ({comparison['income_delta_pct']:+.1f}%)",
            )
        with c2:
            st.metric(
                t("المصاريف", "Expenses"),
                f"{current['expense']:,.2f} {currency_view}",
                delta=f"{comparison['expense_delta']:+,.2f} ({comparison['expense_delta_pct']:+.1f}%)",
                delta_color="inverse",
            )
        with c3:
            st.metric(
                t("الصافي", "Net"),
                f"{current['net']:,.2f} {currency_view}",
                delta=f"{comparison['net_delta']:+,.2f} ({comparison['net_delta_pct']:+.1f}%)",
            )

        s1, s2 = st.columns(2)
        with s1:
            st.write(format_i18n("transactions_this_month", count=current["count"]))
        with s2:
            st.write(format_i18n("transactions_previous_month", count=previous["count"]))

    with st.expander(
        _section_label(
            t("الاستحقاقات والتغطية", "Entitlements and Coverage"),
            f"{t('صافي التغطية', 'Coverage Net')} {coverage['net_coverage']:,.2f} {currency_view}",
        ),
        expanded=False,
    ):
        r1, r2, r3 = st.columns(3)
        with r1:
            st.metric(t("عدد العناصر الشهرية المفعلة", "Active Monthly Items"), f"{len(active_items)}")
        with r2:
            st.metric(t("إجمالي الدخل المتوقع غير المستلم", "Total Expected Income Not Received"), f"{coverage['expected_income']:,.2f} {currency_view}")
        with r3:
            st.metric(t("إجمالي الالتزامات المتأخرة", "Total Overdue Commitments"), f"{coverage['overdue_commitments']:,.2f} {currency_view}")

        st.caption(
            format_i18n(
                "recorded_vs_unconfirmed",
                income=current["income"],
                expense=current["expense"],
                expected=coverage["expected_income"],
                commitments=coverage["overdue_commitments"],
                currency=currency_view,
            )
        )

        u1, u2, u3 = st.columns(3)
        with u1:
            st.metric(t("الصافي الفعلي الآن", "Actual Net Now"), f"{current['net']:,.2f} {currency_view}")
        with u2:
            st.metric(
                t("صافي الاستحقاقات غير المؤكدة", "Unconfirmed Entitlement Net"),
                f"{coverage['net_coverage']:,.2f} {currency_view}",
                delta=format_i18n(
                    "expected_minus_commitments",
                    expected=coverage["expected_income"],
                    commitments=coverage["overdue_commitments"],
                    currency=currency_view,
                ),
                delta_color="off",
            )
        with u3:
            st.metric(
                t("صافي متوقع بعد التحصيل/السداد", "Expected Net After Settle"),
                f"{projected_net_after_pending:,.2f} {currency_view}",
                delta=t("تقدير", "Estimate"),
                delta_color="off",
            )

        k1, k2, k3 = st.columns(3)
        with k1:
            st.metric(
                t("التزامات متأخرة (غير مسددة)", "Overdue Commitments (Unpaid)"),
                f"{coverage['overdue_commitments']:,.2f} {currency_view}",
                delta=format_i18n(
                    "items_months_count",
                    items=coverage["overdue_count"],
                    months=coverage.get("overdue_pending_months", coverage["overdue_count"]),
                ),
                delta_color="inverse",
            )
        with k2:
            st.metric(
                t("دخل متوقع غير مستلم", "Expected Income Not Received"),
                f"{coverage['expected_income']:,.2f} {currency_view}",
                delta=format_i18n(
                    "items_months_count",
                    items=coverage["expected_count"],
                    months=coverage.get("expected_pending_months", coverage["expected_count"]),
                ),
            )
        with k3:
            st.metric(
                t("صافي تغطية الاستحقاقات", "Entitlement Coverage Net"),
                f"{coverage['net_coverage']:,.2f} {currency_view}",
            )

        if coverage["net_coverage"] > 0:
            st.success(t("التغطية المستقبلية إيجابية بعد التحصيل.", "Future coverage is positive after collection."))
        elif coverage["net_coverage"] < 0:
            st.warning(t("في فجوة تغطية حالياً وتحتاج متابعة.", "There is a current coverage gap that needs follow-up."))
        else:
            st.info(t("الوضع متعادل تقريباً بين الالتزامات والدخل المتوقع.", "The situation is nearly balanced between commitments and expected income."))

    with st.expander(
        _section_label(
            t("تدفق نقدي 90 يوم", "90-Day Cash Flow"),
            f"{t('صافي متوقع', 'Projected Net')} {projected_90['net']:,.2f} {currency_view}",
        ),
        expanded=False,
    ):
        f1, f2, f3 = st.columns(3)
        with f1:
            st.metric(
                t("صافي آخر 90 يوم", "Net Last 90 Days"),
                f"{actual_90['net']:,.2f} {currency_view}",
                delta=format_i18n(
                    "income_expense_delta",
                    income=actual_90["income"],
                    expense=actual_90["expense"],
                    currency=currency_view,
                ),
                delta_color="off",
            )
        with f2:
            st.metric(
                t("صافي متوقع خلال 90 يوم", "Projected Net Next 90 Days"),
                f"{projected_90['net']:,.2f} {currency_view}",
                delta=format_i18n(
                    "income_expense_delta",
                    income=projected_90["income"],
                    expense=projected_90["expense"],
                    currency=currency_view,
                ),
                delta_color="off",
            )
        with f3:
            st.metric(
                t("الفرق عن آخر 90 يوم", "Delta vs Last 90 Days"),
                f"{comparison_90['net_delta']:+,.2f} {currency_view}",
                delta=t("تقديري", "Estimate"),
                delta_color="normal" if comparison_90["net_delta"] >= 0 else "inverse",
            )

        g1, g2, g3 = st.columns(3)
        with g1:
            st.metric(
                t("المتكرر المتوقع", "Recurring Planned"),
                f"{(components_90['recurring_income'] - components_90['recurring_expense']):,.2f} {currency_view}",
                delta=format_i18n(
                    "income_expense_delta",
                    income=components_90["recurring_income"],
                    expense=components_90["recurring_expense"],
                    currency=currency_view,
                ),
                delta_color="off",
            )
        with g2:
            st.metric(
                t("تحصيل فواتير متوقع", "Open Invoice Inflow"),
                f"{components_90['invoice_income']:,.2f} {currency_view}",
                delta=format_i18n(
                    "current_overdue_amount",
                    amount=carry_over["overdue_open_invoice_total"],
                    currency=currency_view,
                ),
                delta_color="off",
            )
        with g3:
            st.metric(
                t("رسوم مستندات قريبة", "Upcoming Document Fees"),
                f"{components_90['document_expense']:,.2f} {currency_view}",
                delta=format_i18n(
                    "current_overdue_amount",
                    amount=carry_over["overdue_document_fee_total"],
                    currency=currency_view,
                ),
                delta_color="inverse" if components_90["document_expense"] > 0 else "normal",
            )

        st.caption(
            t(
                "آخر 90 يوم = الفعلي المسجل فقط. الـ 90 يوم القادمة = المتوقع من المتكرر والفواتير المفتوحة ورسوم المستندات. الاستحقاقات الحالية تبقى منفصلة للوضوح.",
                "Last 90 days = recorded actual movement only. Next 90 days = projection from recurring items, open invoices, and upcoming document fees. Current entitlements stay separate for clarity.",
            )
        )

        h1, h2, h3 = st.columns(3)
        with h1:
            st.metric(
                t("صافي الاستحقاقات الحالية", "Current Entitlement Net"),
                f"{carry_over['recurring_delayed_net']:,.2f} {currency_view}",
                delta=format_i18n(
                    "expected_income_commitments_delta",
                    expected=carry_over["delayed_income"],
                    commitments=carry_over["overdue_commitments"],
                    currency=currency_view,
                ),
                delta_color="off",
            )
        with h2:
            st.metric(
                t("فواتير متأخرة مفتوحة", "Overdue Open Invoices"),
                f"{carry_over['overdue_open_invoice_total']:,.2f} {currency_view}",
                delta=format_i18n(
                    "invoice_count",
                    count=carry_over["overdue_open_invoice_count"],
                ),
                delta_color="off",
            )
        with h3:
            st.metric(
                t("رسوم مستندات متأخرة", "Overdue Document Fees"),
                f"{carry_over['overdue_document_fee_total']:,.2f} {currency_view}",
                delta=format_i18n(
                    "document_count",
                    count=carry_over["overdue_document_count"],
                ),
                delta_color="inverse" if carry_over["overdue_document_fee_total"] > 0 else "normal",
            )

        monthly_projection_rows = cash_flow.get("monthly_projection", [])
        if monthly_projection_rows:
            monthly_projection_df = pd.DataFrame(
                [
                    {
                        t("الفترة", "Period"): f"{row['year']:04d}-{row['month']:02d}",
                        t("الدخل المتوقع", "Projected Income"): f"{row['income']:,.2f} {currency_view}",
                        t("المصروف المتوقع", "Projected Expense"): f"{row['expense']:,.2f} {currency_view}",
                        t("الصافي المتوقع", "Projected Net"): f"{row['net']:,.2f} {currency_view}",
                        t("عدد العناصر", "Items"): row["count"],
                    }
                    for row in monthly_projection_rows
                ]
            )
            st.markdown(f"#### {t('تقسيم 90 يوم حسب الشهر', '90-Day Monthly Split')}")
            st.dataframe(monthly_projection_df, hide_index=True)

    with st.expander(
        _section_label(
            t("التوفير والمشاريع", "Savings and Projects"),
            f"{t('صافي المشاريع', 'Projects Net')} {project['month_net']:,.2f} {currency_view}",
        ),
        expanded=False,
    ):
        p1, p2, p3 = st.columns(3)
        with p1:
            st.metric(t("صافي التوفير (كل الشهور)", "Savings Net (All Months)"), f"{savings['all_net']:,.2f} {currency_view}")
        with p2:
            st.metric(t("صافي المشاريع لهذا الشهر", "Projects Net This Month"), f"{project['month_net']:,.2f} {currency_view}")
        with p3:
            st.metric(
                t("صافي المشاريع (كل الشهور)", "Projects Net (All Months)"),
                f"{project['all_net']:,.2f} {currency_view}",
                delta=format_i18n("transaction_count", count=project["all_count"]),
            )

        x1, x2, x3 = st.columns(3)
        with x1:
            st.metric(
                t("الدعم المطلوب للمشاريع", "Estimated Project Support"),
                f"{project_impact['estimated_personal_support']:,.2f} {currency_view}",
                delta=format_i18n(
                    "three_month_deficit",
                    amount=project_impact["last_3m_project_deficit"],
                    currency=currency_view,
                ),
                delta_color="inverse",
            )
        with x2:
            st.metric(
                t("صافي الحساب قبل الدعم", "Personal Net Before Support"),
                f"{project_impact['personal_net']:,.2f} {currency_view}",
            )
        with x3:
            st.metric(
                t("صافي الحساب بعد دعم المشاريع", "Personal Net After Support"),
                f"{project_impact['personal_net_after_support']:,.2f} {currency_view}",
                delta=format_i18n(
                    "deficit_months_last_3",
                    count=project_impact["deficit_months_in_last_3"],
                ),
                delta_color="inverse" if project_impact["personal_net_after_support"] < 0 else "normal",
            )

    with st.expander(
        _section_label(
            t("السلوك الموسمي للمصاريف", "Seasonal Expense Behavior"),
            f"{t('مصروف الشهر', 'Current Expense')} {seasonal['current_expense']:,.2f} {currency_view}",
        ),
        expanded=False,
    ):
        z1, z2, z3 = st.columns(3)
        with z1:
            st.metric(
                t("مصروف آخر شهر", "Current Expense"),
                f"{seasonal['current_expense']:,.2f} {currency_view}",
                delta=f"{seasonal['delta_from_avg']:+,.2f} ({seasonal['delta_pct']:+.1f}%)",
                delta_color="inverse",
            )
        with z2:
            st.metric(
                t("متوسط 6 أشهر", "6-Month Average"),
                f"{seasonal['average_expense']:,.2f} {currency_view}",
            )
        with z3:
            st.metric(
                t("أعلى شهر صرف", "Peak Expense Month"),
                f"{seasonal['peak_expense']:,.2f} {currency_view}",
                delta=seasonal["peak_month"],
                delta_color="off",
            )

        if seasonal["status"] == "high":
            st.info(t("مصاريفك أعلى من متوسط آخر 6 أشهر.", "Your spending is above the 6-month average."))
        elif seasonal["status"] == "low":
            st.info(t("مصاريفك أقل من متوسط آخر 6 أشهر.", "Your spending is below the 6-month average."))
        else:
            st.info(t("مصاريفك قريبة من متوسطك المعتاد.", "Your spending is close to your usual average."))

        if category_signal["top_category"]:
            top_category = category_label(category_signal["top_category"], is_en)
            if category_signal["status"] == "high":
                st.caption(
                    format_i18n(
                        "top_category_above",
                        category=top_category,
                    )
                )
            elif category_signal["status"] == "low":
                st.caption(
                    format_i18n(
                        "top_category_below",
                        category=top_category,
                    )
                )
            else:
                st.caption(
                    format_i18n(
                        "top_category_near",
                        category=top_category,
                    )
                )

    with st.expander(
        _section_label(
            t("المستندات", "Documents"),
            f"{t('عدد المستندات', 'Documents Count')} {docs['count']}",
        ),
        expanded=False,
    ):
        d1, d2, d3 = st.columns(3)
        with d1:
            st.metric(t("عدد المستندات", "Documents Count"), f"{docs['count']}")
        with d2:
            st.metric(t("تنتهي خلال 30 يوم", "Expiring in 30 Days"), f"{docs['upcoming_30_count']}")
        with d3:
            st.metric(
                t("رسوم سنوية تقديرية", "Estimated Annual Fees"),
                f"{docs['annual_fees_estimate']:,.2f} {currency_view}",
                delta=format_i18n("expired_count", count=docs["expired_count"]),
                delta_color="inverse" if docs["expired_count"] > 0 else "normal",
            )

    st.caption(format_i18n("last_update", date=date.today().isoformat()))

from __future__ import annotations

from typing import Any


def _money(value: Any) -> float:
    try:
        return round(float(value or 0.0), 3)
    except (TypeError, ValueError):
        return 0.0


class FinancialCalmBriefEngine:
    """
    Builds a deterministic three-decision brief from precomputed finance facts.

    Python owns every displayed financial number. AI may explain a selected
    decision, but it never supplies titles, metrics, actions, or priorities.
    """

    SCHEMA_VERSION = "financial-calm-brief-v1"

    @staticmethod
    def _decision(
        *,
        decision_id: str,
        priority: int,
        metric_value: float,
        metric_label_ar: str,
        metric_label_en: str,
        title_ar: str,
        title_en: str,
        fallback_explanation_ar: str,
        fallback_explanation_en: str,
        action_ar: str,
        action_en: str,
        fact_ids: list[str],
        metric_kind: str = "money",
        tone: str = "neutral",
    ) -> dict:
        return {
            "decision_id": decision_id,
            "priority": int(priority),
            "tone": tone,
            "title_ar": title_ar,
            "title_en": title_en,
            "metric": {
                "value": _money(metric_value),
                "kind": metric_kind,
                "label_ar": metric_label_ar,
                "label_en": metric_label_en,
            },
            "fallback_explanation_ar": fallback_explanation_ar,
            "fallback_explanation_en": fallback_explanation_en,
            "action_ar": action_ar,
            "action_en": action_en,
            "fact_ids": list(fact_ids),
        }

    @staticmethod
    def _fact(fact_id: str, value: Any, *, kind: str = "money") -> dict:
        return {
            "fact_id": fact_id,
            "kind": kind,
            "value": _money(value),
        }

    def build(
        self,
        *,
        month_key: str,
        currency: str,
        current: dict,
        comparison: dict,
        coverage: dict,
        cash_flow: dict,
        savings: dict,
        seasonal: dict,
        category_signal: dict,
    ) -> dict:
        projected = cash_flow.get("projected_next_90", {})
        carry_over = cash_flow.get("carry_over", {})

        projected_net = _money(projected.get("net"))
        current_net = _money(current.get("net"))
        net_delta = _money(comparison.get("net_delta"))
        coverage_net = _money(coverage.get("net_coverage"))
        coverage_gap = _money(max(0.0, -coverage_net))
        overdue_follow_up = _money(
            _money(carry_over.get("overdue_commitments"))
            + _money(carry_over.get("overdue_open_invoice_total"))
            + _money(carry_over.get("overdue_document_fee_total"))
        )
        category_current = _money(category_signal.get("current_amount"))
        category_average = _money(category_signal.get("avg_amount"))
        category_delta = _money(max(0.0, category_current - category_average))
        seasonal_delta = _money(max(0.0, seasonal.get("delta_from_avg", 0.0)))
        savings_goal = _money(savings.get("month_goal"))
        savings_net = _money(savings.get("month_net"))
        savings_gap = _money(max(0.0, savings_goal - savings_net))
        transaction_count = int(_money(current.get("count")))

        facts = [
            self._fact("projected_net_90d", projected_net),
            self._fact("current_month_net", current_net),
            self._fact("month_net_delta", net_delta),
            self._fact("coverage_net", coverage_net),
            self._fact("coverage_gap", coverage_gap),
            self._fact("overdue_follow_up", overdue_follow_up),
            self._fact("category_current", category_current),
            self._fact("category_average", category_average),
            self._fact("category_delta", category_delta),
            self._fact("seasonal_delta", seasonal_delta),
            self._fact("savings_gap", savings_gap),
            self._fact("transaction_count", transaction_count, kind="count"),
        ]

        candidates: list[dict] = []

        if overdue_follow_up > 0:
            candidates.append(
                self._decision(
                    decision_id="follow_up_open_items",
                    priority=100,
                    metric_value=overdue_follow_up,
                    metric_label_ar="مبالغ مفتوحة ومتأخرة",
                    metric_label_en="Open and overdue",
                    title_ar="تابعي المبالغ المفتوحة أولًا",
                    title_en="Follow up open amounts first",
                    fallback_explanation_ar="تحصيل المبالغ المفتوحة أو تسوية المتأخر منها يخفف الضغط قبل الالتزامات القادمة.",
                    fallback_explanation_en="Collecting open amounts or settling overdue items reduces pressure before upcoming commitments.",
                    action_ar="راجعي العناصر المفتوحة وحددي ما سيُحصّل أو يُسدّد أولًا.",
                    action_en="Review open items and choose what to collect or settle first.",
                    fact_ids=["overdue_follow_up"],
                    tone="attention",
                )
            )

        if category_signal.get("status") == "high" and category_delta > 0:
            category_name = str(category_signal.get("top_category") or "").strip()
            category_name_ar = str(category_signal.get("top_category_ar") or category_name).strip()
            category_name_en = str(category_signal.get("top_category_en") or category_name).strip()
            title_suffix_ar = f" في {category_name_ar}" if category_name_ar else ""
            title_suffix_en = f" in {category_name_en}" if category_name_en else ""
            candidates.append(
                self._decision(
                    decision_id="reduce_category_spike",
                    priority=90,
                    metric_value=category_delta,
                    metric_label_ar="فوق المتوسط",
                    metric_label_en="Above average",
                    title_ar=f"هدّئي الزيادة{title_suffix_ar}",
                    title_en=f"Calm the increase{title_suffix_en}",
                    fallback_explanation_ar="هذه الفئة أعلى من نمطها السابق، لذلك هي أوضح مكان لتخفيف الصرف بدون تغييرات كبيرة.",
                    fallback_explanation_en="This category is above its earlier pattern, making it the clearest place to ease spending without a major change.",
                    action_ar="ضعي سقفًا بسيطًا لهذه الفئة حتى نهاية الشهر.",
                    action_en="Set a simple cap for this category through month end.",
                    fact_ids=["category_current", "category_average", "category_delta"],
                    tone="attention",
                )
            )
        elif seasonal.get("status") == "high" and seasonal_delta > 0:
            candidates.append(
                self._decision(
                    decision_id="reduce_monthly_spending_spike",
                    priority=88,
                    metric_value=seasonal_delta,
                    metric_label_ar="فوق المتوسط الشهري",
                    metric_label_en="Above monthly average",
                    title_ar="هدّئي ارتفاع مصروف الشهر",
                    title_en="Calm this month's spending rise",
                    fallback_explanation_ar="مصروف الشهر أعلى من نمطه المعتاد، ومراجعته الآن تمنع استمرار الزيادة.",
                    fallback_explanation_en="This month's spending is above its usual pattern, and reviewing it now can keep the increase from continuing.",
                    action_ar="راجعي أكبر المصاريف وحددي بندًا واحدًا يمكن تخفيفه.",
                    action_en="Review the largest expenses and choose one item to ease.",
                    fact_ids=["seasonal_delta"],
                    tone="attention",
                )
            )

        if coverage_net < 0:
            candidates.append(
                self._decision(
                    decision_id="close_coverage_gap",
                    priority=95,
                    metric_value=abs(coverage_net),
                    metric_label_ar="فجوة تغطية",
                    metric_label_en="Coverage gap",
                    title_ar="أغلقي فجوة الاستحقاقات",
                    title_en="Close the entitlement gap",
                    fallback_explanation_ar="الالتزامات غير المسددة أعلى من الدخل المتوقع، لذلك تحتاج الأولويات إلى ترتيب قبل أي صرف مرن.",
                    fallback_explanation_en="Unsettled commitments are above expected income, so priorities need ordering before flexible spending.",
                    action_ar="رتبي الاستحقاقات حسب الموعد وثبتي مصدر تغطية لكل منها.",
                    action_en="Order entitlements by due date and assign a funding source to each.",
                    fact_ids=["coverage_gap"],
                    tone="risk",
                )
            )

        candidates.append(
            self._decision(
                decision_id="protect_cash_outlook",
                priority=85 if projected_net < 0 else 60,
                metric_value=projected_net,
                metric_label_ar="صافي التدفق القادم",
                metric_label_en="Upcoming cash-flow net",
                title_ar="احمي هدوء التدفق النقدي",
                title_en="Protect cash-flow calm",
                fallback_explanation_ar=(
                    "التوقع الحالي يحتاج احتياطًا واضحًا قبل إضافة التزامات جديدة."
                    if projected_net < 0
                    else "التوقع الحالي يعطي مساحة، والأفضل حمايتها قبل إضافة التزامات جديدة."
                ),
                fallback_explanation_en=(
                    "The current outlook needs a clear buffer before taking on new commitments."
                    if projected_net < 0
                    else "The current outlook provides room, and protecting it before new commitments is the calmer choice."
                ),
                action_ar="ثبتي احتياطًا للتدفق القادم قبل المصاريف الاختيارية.",
                action_en="Protect a buffer for upcoming cash flow before optional spending.",
                fact_ids=["projected_net_90d"],
                tone="risk" if projected_net < 0 else "calm",
            )
        )

        if len(candidates) < 3:
            candidates.append(
                self._decision(
                    decision_id="strengthen_data_readiness",
                    priority=20,
                    metric_value=transaction_count,
                    metric_kind="count",
                    metric_label_ar="حركات مسجلة هذا الشهر",
                    metric_label_en="Transactions recorded this month",
                    title_ar="قوّي وضوح قراراتك",
                    title_en="Strengthen decision clarity",
                    fallback_explanation_ar="وضوح البيانات يجعل ترتيب القرارات أكثر ثباتًا ويقلل الاعتماد على التقدير.",
                    fallback_explanation_en="Clearer data makes decision ranking steadier and reduces reliance on estimates.",
                    action_ar="سجلي الحركات الناقصة قبل مراجعة الملخص مرة ثانية.",
                    action_en="Record missing activity before reviewing the brief again.",
                    fact_ids=["transaction_count"],
                    tone="neutral",
                )
            )

        if savings_gap > 0:
            candidates.append(
                self._decision(
                    decision_id="restore_savings_pace",
                    priority=55,
                    metric_value=savings_gap,
                    metric_label_ar="المتبقي لهدف التوفير",
                    metric_label_en="Remaining savings goal",
                    title_ar="ارجعي لهدوء خطة التوفير",
                    title_en="Restore the savings pace",
                    fallback_explanation_ar="هدف التوفير ما زال يحتاج دفعة، ويمكن تقسيمها بدل الضغط على دفعة واحدة.",
                    fallback_explanation_en="The savings goal still needs progress, and spreading it out avoids pressure from one large move.",
                    action_ar="قسّمي المتبقي على دفعات صغيرة مرتبطة بمواعيد الدخل.",
                    action_en="Split the remainder into smaller moves tied to income dates.",
                    fact_ids=["savings_gap"],
                    tone="calm",
                )
            )

        candidates.append(
            self._decision(
                decision_id="steady_current_month",
                priority=50 if current_net < 0 else 40,
                metric_value=current_net,
                metric_label_ar="صافي هذا الشهر",
                metric_label_en="This month's net",
                title_ar="ثبتي مسار هذا الشهر",
                title_en="Steady this month's path",
                fallback_explanation_ar=(
                    "صافي الشهر يحتاج تخفيفًا سريعًا للمصاريف المرنة."
                    if current_net < 0
                    else "صافي الشهر متماسك، والمحافظة على نفس الإيقاع تدعم القرارات القادمة."
                ),
                fallback_explanation_en=(
                    "This month's net needs a quick reduction in flexible spending."
                    if current_net < 0
                    else "This month's net is steady, and keeping the same pace supports upcoming decisions."
                ),
                action_ar=(
                    "أوقفي بندًا مرنًا مؤقتًا وراجعي الصافي بعد ذلك."
                    if current_net < 0
                    else "استمري على نفس الإيقاع وراجعي أي التزام جديد قبل اعتماده."
                ),
                action_en=(
                    "Pause one flexible item and review the net again."
                    if current_net < 0
                    else "Keep the same pace and review any new commitment before accepting it."
                ),
                fact_ids=["current_month_net", "month_net_delta"],
                tone="risk" if current_net < 0 else "calm",
            )
        )

        ranked = sorted(candidates, key=lambda item: (-item["priority"], item["decision_id"]))[:3]
        for position, decision in enumerate(ranked, start=1):
            decision["rank"] = position

        return {
            "schema_version": self.SCHEMA_VERSION,
            "month_key": str(month_key or ""),
            "as_of": str(cash_flow.get("as_of") or ""),
            "currency": str(currency or ""),
            "facts": facts,
            "decisions": ranked,
        }

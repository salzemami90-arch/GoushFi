from __future__ import annotations

import json
import os
import re
from hashlib import sha256
from typing import Any, Callable

import requests


SENSITIVE_KEY_FRAGMENTS = {
    "access_token",
    "refresh_token",
    "authorization",
    "password",
    "secret",
    "api_key",
    "apikey",
    "anon_key",
    "auth_code",
    "code_verifier",
    "pkce",
    "cookie",
    "attachment_bytes",
    "proof_bytes",
    "profile_image",
}

DEFAULT_OPENAI_MODEL = "gpt-5.6"

DEFAULT_RESULT = {
    "ok": False,
    "summary": "",
    "risks": [],
    "opportunities": [],
    "next_actions": [],
    "data_gaps": [],
    "confidence": "low",
    "error": "",
}

DEFAULT_CALM_RESULT = {
    "ok": False,
    "explanations": {},
    "error": "",
}

NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety", "hundred", "thousand", "million", "billion", "percent", "percentage",
    "صفر", "واحد", "واحدة", "اثنان", "اثنين", "اثنتان", "اثنتين", "ثلاثة", "ثلاث",
    "أربعة", "اربعة", "أربع", "اربع", "خمسة", "خمس", "ستة", "ست", "سبعة", "سبع",
    "ثمانية", "ثمان", "تسعة", "تسع", "عشرة", "عشر", "مئة", "مائة", "ألف", "الف",
    "مليون", "مليار", "بالمئة", "نسبة",
}


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


class AIInsightsService:
    """Read-only AI brief generator over sanitized finance summaries."""

    def __init__(
        self,
        api_key: str = "",
        *,
        model: str = DEFAULT_OPENAI_MODEL,
        api_url: str = "https://api.openai.com/v1/chat/completions",
        timeout_sec: int = 25,
        post_func: Callable[..., Any] | None = None,
    ):
        self.api_key = str(api_key or "").strip()
        self.model = str(model or DEFAULT_OPENAI_MODEL).strip()
        self.api_url = str(api_url or "https://api.openai.com/v1/chat/completions").strip()
        self.timeout_sec = int(timeout_sec or 25)
        self._post = post_func or requests.post

    @classmethod
    def from_runtime(cls, secrets: Any = None) -> "AIInsightsService":
        secret_sources = [secrets] if secrets is not None else []
        for section_name in ("openai", "OPENAI", "ai", "AI"):
            section = _runtime_section(secrets, section_name)
            if section is not None:
                secret_sources.append(section)

        api_key = ""
        model = ""
        api_url = ""
        for source in secret_sources:
            if not api_key:
                api_key = _runtime_value(source, "OPENAI_API_KEY", "openai_api_key", "api_key")
            if not model:
                model = _runtime_value(source, "OPENAI_MODEL", "openai_model", "model")
            if not api_url:
                api_url = _runtime_value(source, "OPENAI_API_URL", "openai_api_url", "api_url")

        return cls(
            api_key or os.getenv("OPENAI_API_KEY", ""),
            model=model or os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
            api_url=api_url or os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions"),
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_url and self.model)

    @staticmethod
    def _is_sensitive_key(key: str) -> bool:
        normalized = str(key or "").strip().lower()
        return any(fragment in normalized for fragment in SENSITIVE_KEY_FRAGMENTS)

    @classmethod
    def sanitize_context(cls, value: Any, *, max_list_items: int = 16, max_string_chars: int = 900) -> Any:
        if isinstance(value, dict):
            clean = {}
            for key, item in value.items():
                if cls._is_sensitive_key(str(key)):
                    continue
                clean[str(key)] = cls.sanitize_context(
                    item,
                    max_list_items=max_list_items,
                    max_string_chars=max_string_chars,
                )
            return clean

        if isinstance(value, list):
            return [
                cls.sanitize_context(item, max_list_items=max_list_items, max_string_chars=max_string_chars)
                for item in value[:max_list_items]
            ]

        if isinstance(value, (bytes, bytearray)):
            return "[removed_binary]"

        if isinstance(value, str):
            return value[:max_string_chars]

        if isinstance(value, (int, float, bool)) or value is None:
            return value

        return str(value)[:max_string_chars]

    @classmethod
    def context_snapshot(cls, context: dict) -> str:
        clean_context = cls.sanitize_context(context)
        raw = json.dumps(clean_context, ensure_ascii=False, sort_keys=True, default=str)
        return sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item or "").strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @classmethod
    def _normalize_result(cls, raw: Any) -> dict:
        result = DEFAULT_RESULT.copy()
        result["ok"] = True
        if not isinstance(raw, dict):
            result["ok"] = False
            result["error"] = "AI response was not valid JSON."
            return result

        result["summary"] = str(raw.get("summary") or "").strip()
        result["risks"] = cls._normalize_list(raw.get("risks"))
        result["opportunities"] = cls._normalize_list(raw.get("opportunities"))
        result["next_actions"] = cls._normalize_list(raw.get("next_actions"))
        result["data_gaps"] = cls._normalize_list(raw.get("data_gaps"))
        confidence = str(raw.get("confidence") or "low").strip().lower()
        result["confidence"] = confidence if confidence in {"low", "medium", "high"} else "low"
        return result

    @staticmethod
    def _parse_model_json(content: str) -> dict:
        clean_content = str(content or "").strip()
        if clean_content.startswith("```"):
            clean_content = clean_content.strip("`")
            if clean_content.lower().startswith("json"):
                clean_content = clean_content[4:].strip()
        parsed = json.loads(clean_content)
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _explanation_contains_number(text: str) -> bool:
        clean_text = str(text or "")
        if re.search(r"[0-9\u0660-\u0669\u06F0-\u06F9]", clean_text):
            return True
        words = {
            word.casefold()
            for word in re.findall(r"[A-Za-z\u0600-\u06FF]+", clean_text)
        }
        return bool(words.intersection(NUMBER_WORDS))

    @classmethod
    def validate_calm_explanations(cls, raw: Any, expected_ids: list[str]) -> dict:
        result = DEFAULT_CALM_RESULT.copy()
        if not isinstance(raw, dict) or not isinstance(raw.get("explanations"), list):
            result["error"] = "AI response did not match the calm brief contract."
            return result

        rows = raw["explanations"]
        if len(rows) != len(expected_ids):
            result["error"] = "AI response did not return the expected decisions."
            return result

        explanations: dict[str, str] = {}
        received_ids: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                result["error"] = "AI response included an invalid explanation."
                return result
            decision_id = str(row.get("decision_id") or "").strip()
            explanation = str(row.get("explanation") or "").strip()
            if decision_id not in expected_ids or decision_id in explanations or not explanation:
                result["error"] = "AI response included an unknown or duplicate decision."
                return result
            # Only user-facing explanation copy is checked. Technical digits in
            # decision_id or the JSON envelope are intentionally allowed.
            if cls._explanation_contains_number(explanation):
                result["error"] = "AI explanation included a number."
                return result
            explanations[decision_id] = explanation
            received_ids.append(decision_id)

        if received_ids != expected_ids:
            result["error"] = "AI response changed the deterministic decision order."
            return result

        return {
            "ok": True,
            "explanations": explanations,
            "error": "",
        }

    def generate_financial_calm_explanations(self, brief: dict, *, language: str = "ar") -> dict:
        expected_ids = [
            str(item.get("decision_id") or "").strip()
            for item in brief.get("decisions", [])
            if isinstance(item, dict)
        ]
        if len(expected_ids) != 3 or len(set(expected_ids)) != 3 or any(not item for item in expected_ids):
            result = DEFAULT_CALM_RESULT.copy()
            result["error"] = "The deterministic brief must contain exactly three decisions."
            return result

        if not self.is_configured:
            result = DEFAULT_CALM_RESULT.copy()
            result["error"] = "AI is not configured."
            return result

        lang = "English" if str(language or "").lower().startswith("en") else "Arabic"
        sanitized_brief = self.sanitize_context(brief)
        prompt = (
            "You explain a deterministic Financial Calm Brief for GoushFi. "
            "Python has already selected the decisions and owns every financial fact and number. "
            f"Respond in {lang}. Return strict JSON with exactly one key named explanations. "
            "explanations must be an array with exactly one object for each supplied decision. "
            "Each object must contain exactly decision_id and explanation. "
            "Copy decision_id exactly, including any technical digits it may contain. "
            "In explanation, do not write digits, number words, amounts, percentages, dates, counts, "
            "currencies, ranges, or new financial facts. Explain only why the supplied decision matters. "
            "Do not add, remove, merge, reorder, or rename decisions.\n\n"
            f"Deterministic brief:\n{json.dumps(sanitized_brief, ensure_ascii=False, default=str)}"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Return JSON only. Never put quantities in user-facing explanation text.",
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }

        try:
            response = self._post(self.api_url, headers=headers, json=payload, timeout=self.timeout_sec)
        except Exception as exc:
            result = DEFAULT_CALM_RESULT.copy()
            result["error"] = f"Network error: {exc}"
            return result

        try:
            status_code = int(getattr(response, "status_code", 0) or 0)
        except Exception:
            status_code = 0
        if status_code >= 400 or status_code == 0:
            result = DEFAULT_CALM_RESULT.copy()
            result["error"] = f"AI request failed with status {status_code}."
            return result

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return self.validate_calm_explanations(
                self._parse_model_json(content),
                expected_ids,
            )
        except Exception as exc:
            result = DEFAULT_CALM_RESULT.copy()
            result["error"] = f"Could not parse AI response: {exc}"
            return result

    def generate_cash_flow_brief(self, context: dict, *, language: str = "ar") -> dict:
        if not self.is_configured:
            result = DEFAULT_RESULT.copy()
            result["error"] = "AI is not configured."
            return result

        lang = "English" if str(language or "").lower().startswith("en") else "Arabic"
        sanitized_context = self.sanitize_context(context)
        prompt = (
            "You are the CFO AI Assistant for GoushFi, a personal and small-business finance app. "
            "Analyze only the sanitized financial summaries below. Do not invent transactions. "
            f"Respond in {lang}. Return strict JSON with exactly these keys: "
            "summary, risks, opportunities, next_actions, data_gaps, confidence. "
            "Use arrays for risks, opportunities, next_actions, and data_gaps. "
            "confidence must be low, medium, or high.\n\n"
            f"Sanitized financial context:\n{json.dumps(sanitized_context, ensure_ascii=False, default=str)}"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a careful finance assistant. Return JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }

        try:
            response = self._post(self.api_url, headers=headers, json=payload, timeout=self.timeout_sec)
        except Exception as exc:
            result = DEFAULT_RESULT.copy()
            result["error"] = f"Network error: {exc}"
            return result

        try:
            status_code = int(getattr(response, "status_code", 0) or 0)
        except Exception:
            status_code = 0
        if status_code >= 400 or status_code == 0:
            result = DEFAULT_RESULT.copy()
            result["error"] = f"AI request failed with status {status_code}."
            return result

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return self._normalize_result(self._parse_model_json(content))
        except Exception as exc:
            result = DEFAULT_RESULT.copy()
            result["error"] = f"Could not parse AI response: {exc}"
            return result

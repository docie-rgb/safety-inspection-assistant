"""규칙 기반 기준선 분류기 (AI 아님, 로컬 실행).

동작:
1. 원문을 문장(. ! ? 줄바꿈)으로, 문장을 절(쉼표·'는데')로 나눕니다.
2. 분류 체계의 rules(정규식 묶음)가 한 '절' 안에서 모두 맞으면 그 코드로 판정합니다.
   절에 부정 표현(settings.rule.negation_markers)이 있으면 그 절의 판정은 버립니다.
3. 절 단위로 못 찾은 코드는 '문장' 단위로 다시 봅니다. 단, 그 문장 어느 절에도 부정 표현이 없을 때만 인정합니다.
4. 아무 코드도 없으면 정상 표현이 있을 때 NONE, 없으면 UNKNOWN_NEEDS_CHECK.
5. 불확실 표현이 있거나 장소를 알 수 없으면 정보 부족(확인 필요).
규칙과 표현 목록은 모두 config 에 있습니다.
"""
from __future__ import annotations

import re

from .config import load_settings, load_taxonomy

SENT_SPLIT = re.compile(r"[.!?\n]+")
CLAUSE_SPLIT = re.compile(r",|는데")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]


def _clauses(sentence: str) -> list[str]:
    return [c.strip() for c in CLAUSE_SPLIT.split(sentence) if c.strip()]


def _group_match(group: list[str], span: str) -> bool:
    return all(re.search(p, span) for p in group)


def _has_any(markers: list[str], span: str) -> bool:
    return any(m in span for m in markers)


def classify(text: str, location: str = "") -> dict:
    s = load_settings()["rule"]
    tax = load_taxonomy()
    neg, normal, unc = s["negation_markers"], s["normal_markers"], s["uncertainty_markers"]

    found: dict[str, str] = {}  # code -> 근거 문장/절
    for sent in _sentences(text):
        clauses = _clauses(sent)
        sent_negated = any(_has_any(neg, c) for c in clauses)
        for code, spec in tax["codes"].items():
            if code in found:
                continue
            for group in spec.get("rules") or []:
                hit = next((c for c in clauses if _group_match(group, c) and not _has_any(neg, c)), None)
                if hit is None and not sent_negated and _group_match(group, sent):
                    hit = sent
                if hit:
                    found[code] = hit
                    break

    codes = list(found)
    if not codes:
        codes = ["NONE"] if _has_any(normal, text) else ["UNKNOWN_NEEDS_CHECK"]
        found[codes[0]] = text.strip()

    missing = []
    uncertain = _has_any(unc, text)
    if uncertain:
        missing.append("진술에 불확실한 표현이 있어 현장 확인 필요")
    if not location.strip():
        missing.append("장소")
    if codes == ["UNKNOWN_NEEDS_CHECK"]:
        missing.append("위험요인을 특정할 수 있는 설명")
    insufficient = bool(uncertain or not location.strip() or codes == ["UNKNOWN_NEEDS_CHECK"])

    cats = sorted({tax["codes"][c]["category"] for c in codes})
    actions = [a for c in codes for a in tax["codes"][c]["actions"]]
    return {
        # 규칙 기반은 문장을 고쳐 쓰지 않습니다(원문 그대로).
        "cleaned_description": text.strip(),
        "hazard_category": cats,
        "observed_hazards": [
            {"code": c, "name": tax["codes"][c]["name"], "description": tax["codes"][c]["name"], "evidence": found[c]}
            for c in codes
        ],
        "suggested_actions": actions,
        "missing_information": missing,
        "insufficient_information": insufficient,
        "review_required": True,
    }

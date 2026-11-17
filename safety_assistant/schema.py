"""분석 결과의 공통 형식(JSON 스키마)과 정규화."""
from __future__ import annotations

from .config import load_taxonomy

OUTPUT_FIELDS = [
    "cleaned_description",
    "hazard_category",
    "observed_hazards",
    "suggested_actions",
    "missing_information",
    "insufficient_information",
    "review_required",
]


def output_json_schema() -> dict:
    """LLM 구조화 출력에 쓰는 JSON 스키마. 위험유형·위험요인 코드는 분류 체계의 값으로 제한합니다."""
    tax = load_taxonomy()
    return {
        "type": "object",
        "properties": {
            "cleaned_description": {"type": "string"},
            "hazard_category": {
                "type": "array",
                "items": {"type": "string", "enum": list(tax["categories"])},
            },
            "observed_hazards": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "enum": list(tax["codes"].keys())},
                        "description": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["code", "description", "evidence"],
                },
            },
            "suggested_actions": {"type": "array", "items": {"type": "string"}},
            "missing_information": {"type": "array", "items": {"type": "string"}},
            "insufficient_information": {"type": "boolean"},
            "review_required": {"type": "boolean"},
        },
        "required": OUTPUT_FIELDS,
    }


def empty_output(description: str = "") -> dict:
    return {
        "cleaned_description": description,
        "hazard_category": [],
        "observed_hazards": [],
        "suggested_actions": [],
        "missing_information": [],
        "insufficient_information": True,
        "review_required": True,
    }


def normalize_output(obj: dict) -> tuple[dict, list[str]]:
    """모델 출력을 공통 형식으로 맞춥니다. 값을 '고치지' 않고, 형식 문제는 problems 로 기록만 합니다."""
    tax = load_taxonomy()
    problems: list[str] = []
    out = empty_output()
    for k in OUTPUT_FIELDS:
        if k not in obj:
            problems.append(f"필드 누락: {k}")
            continue
        out[k] = obj[k]
    hazards = []
    for h in out.get("observed_hazards") or []:
        if not isinstance(h, dict):
            problems.append(f"위험요인 형식 오류: {h!r}")
            continue
        code = h.get("code", "")
        if code not in tax["codes"]:
            problems.append(f"분류 체계에 없는 코드: {code}")
        hazards.append({
            "code": code,
            "name": tax["codes"].get(code, {}).get("name", "(알 수 없는 코드)"),
            "description": h.get("description", ""),
            "evidence": h.get("evidence", ""),
        })
    out["observed_hazards"] = hazards
    for c in out.get("hazard_category") or []:
        if c not in tax["categories"]:
            problems.append(f"분류 체계에 없는 위험유형: {c}")
    return out, problems

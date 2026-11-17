"""텍스트·음성(전사 후) 입력이 모두 거치는 단일 보고서 생성 엔진."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from . import llm_engine, rule_engine
from .checks import check_output
from .config import load_settings
from .schema import empty_output, normalize_output

RULE_INFO = {
    "backend": "rule",
    "model": "rule_baseline (config/taxonomy.yaml rules)",
    "execution_location": "로컬 PC (Python)",
    "data_transfer": "없음",
    "license": "프로젝트 자체 코드",
}


def analyze(text: str, location: str = "", backend: str | None = None) -> dict:
    """신고 원문을 분석해 결과 레코드를 돌려줍니다.

    반환값의 is_ai_inference 로 실제 AI 추론 결과인지(규칙 기반/미실행이 아닌지) 구분합니다.
    """
    s = load_settings()
    backend = backend or s["llm"]["default_backend"]
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    rec: dict = {
        "backend": backend,
        "is_ai_inference": False,
        "started_at_utc": started,
        "input": {"text": text, "location": location},
        "raw_output": None,
        "backend_meta": None,
        "problems": [],
        "error": None,
    }

    # 설명 없이 사진만 있는 경우 등: 위험요인을 추측하지 않습니다.
    if len((text or "").strip().replace(" ", "")) < s["analysis"]["min_text_chars"]:
        out = empty_output("")
        out["observed_hazards"] = [{"code": "UNKNOWN_NEEDS_CHECK", "name": "위험요인 특정 불가(확인 필요)",
                                    "description": "텍스트·음성 설명이 없어 위험요인을 판단하지 않았습니다.", "evidence": ""}]
        out["hazard_category"] = ["확인필요"]
        out["missing_information"] = ["위험 상황에 대한 텍스트 또는 음성 설명"] + ([] if location.strip() else ["장소"])
        rec.update(info={"backend": "none", "model": "-", "execution_location": "-", "data_transfer": "없음", "license": "-"},
                   output=out, note="설명 없음: 추론하지 않음")
    elif backend == "rule":
        rec["info"] = RULE_INFO
        rec["output"] = rule_engine.classify(text, location)
    else:
        rec["info"] = llm_engine.backend_info(backend)
        system, pfile, phash = llm_engine.build_system_prompt()
        user = llm_engine.build_user_message(text, location)
        rec.update(prompt_file=pfile, prompt_sha256=phash, user_message=user)
        try:
            raw, meta = llm_engine.BACKENDS[backend](system, user)
            rec["raw_output"], rec["backend_meta"] = raw, meta
            out, problems = normalize_output(json.loads(raw))
            rec["output"], rec["problems"] = out, problems
            rec["is_ai_inference"] = True
        except Exception as e:  # noqa: BLE001  실패도 기록으로 남깁니다.
            rec["error"] = f"{type(e).__name__}: {e}"
            rec["error_diagnostics"] = getattr(e, "diagnostics", None)
            rec["output"] = empty_output(text)
            rec["output"]["missing_information"] = ["AI 분석 실패 — 수동 작성 필요"]
    rec["elapsed_sec"] = time.perf_counter() - t0
    rec["checks"] = check_output(text, location, rec["output"])
    return rec


MANUAL_INFO = {
    "backend": "manual",
    "model": "-",
    "execution_location": "사용자 직접 작성",
    "data_transfer": "없음",
    "license": "-",
}


def manual_result(text: str, location: str = "") -> dict:
    """AI·규칙 분석 없이 사용자가 직접 작성하도록 빈 양식을 만듭니다(추론 없음)."""
    out = empty_output(text)
    out["missing_information"] = ["수동 작성 — 위험요인·위험유형을 직접 입력하세요"]
    out["review_required"] = True
    return {
        "backend": "manual",
        "is_ai_inference": False,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": {"text": text, "location": location},
        "info": MANUAL_INFO,
        "output": out,
        "raw_output": None,
        "backend_meta": None,
        "problems": [],
        "error": None,
        "note": "수동 작성 선택: 추론하지 않음",
        "elapsed_sec": 0.0,
        "checks": check_output(text, location, out),
    }

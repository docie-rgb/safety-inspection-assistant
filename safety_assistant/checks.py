"""원문에 없는 사실 추가 여부를 '자동으로 의심'하는 휴리스틱.

주의: 이 검사는 사람 검토를 대신하지 않습니다. 의심 0건이 '사실 추가 0건'을 보장하지 않습니다.
- evidence_not_in_source: 근거 인용이 원문에 없음(원문을 바꿔 쓰거나 지어낸 근거)
- fabrication_terms     : 원문에 없는 부상·재해·법령 관련 표현이 출력에 등장
- location_added        : 장소 입력과 원문이 모두 비었는데 출력 문장에 장소처럼 보이는 표현이 새로 등장(간이 검사)
"""
from __future__ import annotations

import re

from .config import load_settings


def _norm(s: str, ignore: str) -> str:
    return "".join(ch for ch in s if ch not in ignore)


def check_output(text: str, location: str, out: dict) -> dict:
    cfg = load_settings()["analysis"]
    ignore = cfg["evidence_ignore_chars"]
    src = _norm(text, ignore)
    src_all = text + " " + (location or "")

    ev_bad = []
    for h in out.get("observed_hazards", []):
        ev = _norm(h.get("evidence", ""), ignore)
        if h.get("code") in ("NONE", "UNKNOWN_NEEDS_CHECK"):
            continue
        if not ev or ev not in src:
            ev_bad.append({"code": h.get("code"), "evidence": h.get("evidence", "")})

    produced = " ".join(
        [out.get("cleaned_description", "")]
        + [h.get("description", "") for h in out.get("observed_hazards", [])]
    )
    fab = sorted({t for t in cfg["fabrication_terms"] if t in produced and t not in src_all})

    loc_added = []
    if not (location or "").strip():
        for m in re.findall(r"[가-힣0-9]+(?:실|동|라인|부스|창고|보관소|구역|공장)", out.get("cleaned_description", "")):
            if m not in text:
                loc_added.append(m)

    return {
        "evidence_not_in_source": ev_bad,
        "fabrication_terms": fab,
        "location_added": loc_added,
        "suspected_fabrication": bool(fab or loc_added),
    }

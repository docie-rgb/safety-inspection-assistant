"""여러 실행 결과(runs/*)를 한 표로 비교해 Markdown 으로 저장합니다(덮어쓰지 않음).

사용: python scripts/compare_runs.py runs/20261117-142640_rule runs/20261117-142651_ollama
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(run: Path) -> tuple[dict, list[dict]]:
    s = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    with (run / "per_case.csv").open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return s, rows


def main() -> None:
    runs = [ROOT / p if not Path(p).is_absolute() else Path(p) for p in sys.argv[1:]]
    data = [(r.name, *load(r)) for r in runs]
    L = ["# 실행 결과 비교", "", f"생성: {datetime.now().isoformat(timespec='seconds')}", "",
         "원시값은 각 실행 폴더의 summary.json 에 반올림 없이 있습니다. 아래 표의 비율·시간은 읽기 쉽게 반올림한 표시값입니다.", ""]
    metrics = [
        ("정답 일치(위험유형+핵심 위험요인) 건수", lambda b: f"{b['full_match_count']}/{b['n']}"),
        ("위험유형 일치", lambda b: f"{b['category_match_count']}/{b['n']}"),
        ("핵심 위험요인 일치", lambda b: f"{b['hazard_match_count']}/{b['n']}"),
        ("위험요인 미시 정밀도", lambda b: f"{b['hazard_micro_precision']:.3f}"),
        ("위험요인 미시 재현율", lambda b: f"{b['hazard_micro_recall']:.3f}"),
        ("정보부족 판정 일치", lambda b: f"{b['insufficient_match_count']}/{b['n']}"),
        ("정보부족 사례 중 확인필요 표시", lambda b: f"{b['gold_insufficient_flagged']}/{b['gold_insufficient_cases']}"),
        ("정보충분 사례를 확인필요로 표시(과잉)", lambda b: f"{b['false_insufficient_flags']}"),
        ("사실 추가 의심(자동 휴리스틱)", lambda b: f"{b['suspected_fabrication_count']}"),
        ("근거 인용이 원문에 없는 사례", lambda b: f"{b['cases_with_evidence_not_in_source']}"),
        ("실행 오류", lambda b: f"{b['error_count']}"),
        ("평균 처리 시간(초/건)", lambda b: f"{b['elapsed_sec_mean']:.3f}"),
        ("최대 처리 시간(초)", lambda b: f"{b['elapsed_sec_max']:.3f}"),
    ]
    for scope, title in [("all", "전체 100건"), ("normal", "일반 80건"), ("hard", "어려운 20건")]:
        L += [f"## {title}", "", "| 지표 | " + " | ".join(n for n, _, _ in data) + " |", "|---|" + "---|" * len(data)]
        for name, fn in metrics:
            L.append(f"| {name} | " + " | ".join(fn(s[scope]) for _, s, _ in data) + " |")
        L.append("")
    tags = sorted({t for _, s, _ in data for t in s["hard_by_tag"]})
    L += ["## 어려운 사례 유형별 정답 일치", "", "| 유형 | " + " | ".join(n for n, _, _ in data) + " |", "|---|" + "---|" * len(data)]
    for t in tags:
        L.append(f"| {t} | " + " | ".join(f"{s['hard_by_tag'][t]['full_match_count']}/{s['hard_by_tag'][t]['n']}" for _, s, _ in data) + " |")
    L.append("")
    for name, _, rows in data:
        bad = [r for r in rows if r["full_match"] != "True" or r["insufficient_match"] != "True" or r["suspected_fabrication"] == "True"]
        L += [f"## 실패·불일치 사례: {name} ({len(bad)}건)", "", "| 사례 | 난이도 | 정답 | 예측 | 오류 이유 |", "|---|---|---|---|---|"]
        for r in bad:
            L.append(f"| {r['case_id']} | {r['difficulty']} {r['hard_tags']} | {r['gold_codes']} | {r['pred_codes']} | {r['error_reason'].replace('|', '/')} |")
        L.append("")
    out = ROOT / "runs" / f"{datetime.now():%Y%m%d-%H%M%S}_comparison.md"
    if out.exists():
        sys.exit(f"이미 있음: {out}")
    out.write_text("\n".join(L), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()

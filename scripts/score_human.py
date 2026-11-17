"""사람 수작업 기준선 채점.

사람이 실제로 측정한 경우에만 씁니다. 측정하지 않은 시간·정확도를 임의로 채우지 마세요.

1) 템플릿 만들기:  python scripts/score_human.py --make-template eval/human_sheet.csv
   - case_id, 원문, 장소가 채워진 CSV 가 생깁니다. 정답은 들어 있지 않습니다.
2) 담당자가 각 사례를 읽고 hazard_codes(| 로 구분, config/taxonomy.yaml 코드), hazard_category,
   insufficient(TRUE/FALSE), start_time/end_time(ISO 시각) 을 채웁니다.
3) 채점:          python scripts/score_human.py --sheet eval/human_sheet.csv --worker W1
   - 결과는 runs/<시각>_human_<worker>/ 에 저장됩니다(덮어쓰지 않음).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_eval import read_jsonl, score_case, summarize, verify_gold  # noqa: E402

from safety_assistant.checks import check_output  # noqa: E402
from safety_assistant.config import load_settings, path_of  # noqa: E402

FIELDS = ["case_id", "location", "text", "hazard_codes", "hazard_category", "insufficient", "start_time", "end_time", "notes"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--make-template")
    ap.add_argument("--sheet")
    ap.add_argument("--worker", default="W1", help="가명(실명 금지)")
    a = ap.parse_args()
    if a.make_template:
        p = ROOT / a.make_template
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            sys.exit(f"[중단] {p} 가 이미 있습니다(덮어쓰지 않음).")
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for c in read_jsonl(path_of("cases")):
                w.writerow({"case_id": c["case_id"], "location": c["location"], "text": c["text"]})
        print(p)
        return
    verify_gold()
    gold = {g["case_id"]: g for g in read_jsonl(path_of("gold"))}
    rows = []
    with (ROOT / a.sheet).open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not r.get("hazard_codes"):
                continue  # 사람이 처리하지 않은 사례는 채점하지 않음
            codes = [c for c in r["hazard_codes"].split("|") if c]
            out = {"cleaned_description": r["text"], "hazard_category": [c for c in r["hazard_category"].split("|") if c],
                   "observed_hazards": [{"code": c, "description": "", "evidence": ""} for c in codes],
                   "insufficient_information": r["insufficient"].strip().upper() == "TRUE"}
            el = (datetime.fromisoformat(r["end_time"]) - datetime.fromisoformat(r["start_time"])).total_seconds() \
                if r.get("start_time") and r.get("end_time") else float("nan")
            rec = {"output": out, "elapsed_sec": el, "is_ai_inference": False, "error": None,
                   "checks": check_output(r["text"], r["location"], {"cleaned_description": "", "observed_hazards": []})}
            rows.append(score_case(gold[r["case_id"]], rec))
    if not rows:
        sys.exit("채점할 행이 없습니다.")
    d = ROOT / load_settings()["paths"]["runs_dir"] / f"{datetime.now():%Y%m%d-%H%M%S}_human_{a.worker}"
    d.mkdir(parents=True, exist_ok=False)
    with (d / "per_case.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    s = summarize(rows)
    s["method"] = f"human:{a.worker}"
    (d / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(s["all"], ensure_ascii=False, indent=2))
    print(d)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "scripts"))
    main()

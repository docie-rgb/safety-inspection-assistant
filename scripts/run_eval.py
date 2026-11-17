"""정답 데이터에 대해 한 가지 방식(rule / ollama / anthropic)을 실행하고 채점합니다.

- 실행 전 정답 파일 해시를 GOLD_LOCK.json 과 대조합니다. 다르면 중단합니다.
- 결과는 runs/<시각>_<방식>/ 에 새로 저장하며, 이미 있으면 덮어쓰지 않습니다.
- 원시 점수는 반올림하지 않습니다.

사용 예:
  python scripts/run_eval.py --method rule
  python scripts/run_eval.py --method ollama
  python scripts/run_eval.py --method ollama --limit 5      # 동작 확인용
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from safety_assistant import llm_engine  # noqa: E402
from safety_assistant.config import SETTINGS_PATH, load_settings, load_taxonomy, path_of  # noqa: E402
from safety_assistant.engine import analyze  # noqa: E402


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def verify_gold() -> dict:
    lock = json.loads(path_of("gold_lock").read_text(encoding="utf-8"))
    for rel, h in lock["files"].items():
        actual = sha256(ROOT / rel)
        if actual != h:
            sys.exit(f"[중단] {rel} 해시가 고정값과 다릅니다. 정답이 바뀌었을 수 있습니다.\n  고정: {h}\n  현재: {actual}")
    return lock


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def score_case(gold: dict, rec: dict) -> dict:
    out = rec["output"]
    pred_codes = sorted({h["code"] for h in out.get("observed_hazards", [])})
    pred_cats = sorted(set(out.get("hazard_category", [])))
    g_codes, g_cats = set(gold["hazard_codes"]), set(gold["hazard_category"])
    cat_match = set(pred_cats) == g_cats
    haz_match = set(pred_codes) == g_codes
    insuff_pred = bool(out.get("insufficient_information"))
    reasons = []
    if rec.get("error"):
        reasons.append(f"실행 오류: {rec['error']}")
    fn, fp = sorted(g_codes - set(pred_codes)), sorted(set(pred_codes) - g_codes)
    if fn:
        reasons.append("놓친 위험요인: " + ",".join(fn))
    if fp:
        reasons.append("잘못 추가한 위험요인: " + ",".join(fp))
    if not cat_match:
        reasons.append(f"위험유형 불일치: 예측 {pred_cats} / 정답 {sorted(g_cats)}")
    if insuff_pred != gold["insufficient_information"]:
        reasons.append(f"정보부족 판정 불일치: 예측 {insuff_pred} / 정답 {gold['insufficient_information']}")
    chk = rec["checks"]
    if chk["suspected_fabrication"]:
        reasons.append(f"사실 추가 의심(자동): 용어 {chk['fabrication_terms']} 장소 {chk['location_added']}")
    if chk["evidence_not_in_source"]:
        reasons.append(f"근거 인용이 원문에 없음: {len(chk['evidence_not_in_source'])}건")
    return {
        "case_id": gold["case_id"],
        "difficulty": gold["difficulty"],
        "hard_tags": "|".join(gold["hard_tags"]),
        "text": gold["text"],
        "gold_codes": "|".join(sorted(g_codes)),
        "pred_codes": "|".join(pred_codes),
        "gold_categories": "|".join(sorted(g_cats)),
        "pred_categories": "|".join(pred_cats),
        "category_match": cat_match,
        "hazard_match": haz_match,
        "full_match": cat_match and haz_match,
        "gold_insufficient": gold["insufficient_information"],
        "pred_insufficient": insuff_pred,
        "insufficient_match": insuff_pred == gold["insufficient_information"],
        "suspected_fabrication": chk["suspected_fabrication"],
        "evidence_not_in_source": len(chk["evidence_not_in_source"]),
        "tp": len(g_codes & set(pred_codes)),
        "fp": len(fp),
        "fn": len(fn),
        "elapsed_sec": rec["elapsed_sec"],
        "is_ai_inference": rec["is_ai_inference"],
        "error": rec.get("error") or "",
        "error_reason": " / ".join(reasons),
    }


def summarize(rows: list[dict]) -> dict:
    def block(rs: list[dict]) -> dict:
        n = len(rs)
        if n == 0:
            return {"n": 0}
        tp, fp, fn = (sum(r[k] for r in rs) for k in ("tp", "fp", "fn"))
        gi = [r for r in rs if r["gold_insufficient"]]
        return {
            "n": n,
            "full_match_count": sum(r["full_match"] for r in rs),
            "category_match_count": sum(r["category_match"] for r in rs),
            "hazard_match_count": sum(r["hazard_match"] for r in rs),
            "full_match_rate": sum(r["full_match"] for r in rs) / n,
            "category_match_rate": sum(r["category_match"] for r in rs) / n,
            "hazard_match_rate": sum(r["hazard_match"] for r in rs) / n,
            "hazard_micro_precision": tp / (tp + fp) if tp + fp else None,
            "hazard_micro_recall": tp / (tp + fn) if tp + fn else None,
            "insufficient_match_count": sum(r["insufficient_match"] for r in rs),
            "gold_insufficient_cases": len(gi),
            "gold_insufficient_flagged": sum(r["pred_insufficient"] for r in gi),
            "false_insufficient_flags": sum(r["pred_insufficient"] and not r["gold_insufficient"] for r in rs),
            "suspected_fabrication_count": sum(r["suspected_fabrication"] for r in rs),
            "cases_with_evidence_not_in_source": sum(r["evidence_not_in_source"] > 0 for r in rs),
            "error_count": sum(bool(r["error"]) for r in rs),
            "elapsed_sec_total": sum(r["elapsed_sec"] for r in rs),
            "elapsed_sec_mean": sum(r["elapsed_sec"] for r in rs) / n,
            "elapsed_sec_max": max(r["elapsed_sec"] for r in rs),
        }

    by_tag = defaultdict(list)
    for r in rows:
        for t in filter(None, r["hard_tags"].split("|")):
            by_tag[t].append(r)
    return {
        "all": block(rows),
        "normal": block([r for r in rows if r["difficulty"] == "normal"]),
        "hard": block([r for r in rows if r["difficulty"] == "hard"]),
        "hard_by_tag": {t: block(rs) for t, rs in sorted(by_tag.items())},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", required=True, choices=["rule", "ollama", "anthropic"])
    ap.add_argument("--limit", type=int, default=None, help="앞에서부터 N건만 실행(동작 확인용, 정식 결과 아님)")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    settings = load_settings()
    lock = verify_gold()
    if args.method != "rule":
        ok, msg = llm_engine.is_available(args.method)
        if not ok:
            sys.exit(f"[중단] {args.method} 백엔드를 쓸 수 없습니다: {msg}\n실행하지 않은 결과는 기록하지 않습니다.")

    cases = read_jsonl(path_of("cases"))
    gold = {g["case_id"]: g for g in read_jsonl(path_of("gold"))}
    if args.limit:
        cases = cases[: args.limit]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / settings["paths"]["runs_dir"] / f"{stamp}_{args.method}{'_limit' + str(args.limit) if args.limit else ''}"
    run_dir.mkdir(parents=True, exist_ok=False)  # 이미 있으면 예외: 덮어쓰지 않음

    log = (run_dir / "run.log").open("w", encoding="utf-8")

    def logp(msg: str) -> None:
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    # 실행 조건 기록
    shutil.copy(path_of("cases"), run_dir / "inputs_cases.jsonl")
    shutil.copy(SETTINGS_PATH, run_dir / "settings_snapshot.yaml")
    shutil.copy(ROOT / settings["paths"]["taxonomy"], run_dir / "taxonomy_snapshot.yaml")
    run_cfg = {
        "method": args.method,
        "limit": args.limit,
        "note": args.note,
        "started_at": datetime.now().isoformat(),
        "git_commit": git_commit(),
        "gold_lock": lock,
        "python": sys.version,
    }
    if args.method == "rule":
        from safety_assistant.engine import RULE_INFO
        run_cfg["backend_info"] = RULE_INFO
    else:
        run_cfg["backend_info"] = llm_engine.backend_info(args.method)
        run_cfg["backend_settings"] = settings["llm"]["backends"][args.method]
        prompt, pfile, phash = llm_engine.build_system_prompt()
        (run_dir / "prompt_used.md").write_text(prompt, encoding="utf-8")
        run_cfg.update(prompt_file=pfile, prompt_sha256=phash)
    (run_dir / "run_config.json").write_text(json.dumps(run_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    logp(f"시작: method={args.method} cases={len(cases)} dir={run_dir.name}")
    rows = []
    t_all = time.perf_counter()
    with (run_dir / "outputs.jsonl").open("w", encoding="utf-8") as fo:
        for c in cases:
            rec = analyze(c["text"], c["location"], args.method)
            rec["case_id"] = c["case_id"]
            fo.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fo.flush()
            row = score_case(gold[c["case_id"]], rec)
            rows.append(row)
            logp(f"{c['case_id']} full={row['full_match']} insuff={row['insufficient_match']} "
                 f"t={row['elapsed_sec']:.3f}s {row['error_reason'][:120]}")

    with (run_dir / "per_case.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    summary = summarize(rows)
    summary["wall_clock_sec"] = time.perf_counter() - t_all
    summary["method"] = args.method
    summary["backend_info"] = run_cfg["backend_info"]
    summary["note"] = "suspected_fabrication_count 는 자동 휴리스틱 결과이며, 사람 검토로 확정한 값이 아닙니다."
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    a = summary["all"]
    logp(f"완료: 정답 일치 {a['full_match_count']}/{a['n']}, 위험유형 일치 {a['category_match_count']}, "
         f"위험요인 일치 {a['hazard_match_count']}, 사실추가 의심 {a['suspected_fabrication_count']}, 오류 {a['error_count']}")
    log.close()
    print(run_dir)


if __name__ == "__main__":
    main()

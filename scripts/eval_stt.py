"""STT 경로 평가: (1) 전사 정확도(CER)와 (2) 최종 보고서 정보 보존을 구분해 잽니다.

(1) 전사 정확도: 정답 문장과 전사문의 문자 오류율(CER, 공백·문장부호 제외).
(2) 정보 보존 : 같은 분석 방식으로 '원문 텍스트'와 '전사문'을 각각 분석해 위험요인 코드 집합이 같은지,
               그리고 전사문 분석 결과가 사전 정답과 일치하는지.
결과는 runs/<시각>_stt_<방식>/ 에 저장합니다(덮어쓰지 않음).

사용: python scripts/eval_stt.py --method ollama
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

from safety_assistant import stt  # noqa: E402
from safety_assistant.config import SETTINGS_PATH, load_settings, path_of  # noqa: E402
from safety_assistant.engine import analyze  # noqa: E402

IGNORE = set(" \t\n.,!?\"'·…()")


def cer(ref: str, hyp: str) -> tuple[float, int, int]:
    r = [c for c in ref if c not in IGNORE]
    h = [c for c in hyp if c not in IGNORE]
    prev = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        cur = [i] + [0] * len(h)
        for j in range(1, len(h) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r[i - 1] != h[j - 1]))
        prev = cur
    return prev[-1] / max(len(r), 1), prev[-1], len(r)


def codes(rec: dict) -> list[str]:
    return sorted({h["code"] for h in rec["output"]["observed_hazards"]})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", default="rule", choices=["rule", "ollama", "anthropic"])
    args = ap.parse_args()
    s = load_settings()
    audio_dir = path_of("audio_dir")
    manifest = json.loads((audio_dir / "manifest.json").read_text(encoding="utf-8"))
    cases = {json.loads(l)["case_id"]: json.loads(l) for l in path_of("cases").read_text(encoding="utf-8").splitlines() if l.strip()}
    gold = {json.loads(l)["case_id"]: json.loads(l) for l in path_of("gold").read_text(encoding="utf-8").splitlines() if l.strip()}

    run_dir = ROOT / s["paths"]["runs_dir"] / f"{datetime.now():%Y%m%d-%H%M%S}_stt_{args.method}"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "settings_snapshot.yaml").write_text(SETTINGS_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    rows, full = [], []
    for m in manifest:
        cid = m["case_id"]
        tr = stt.transcribe(str(audio_dir / m["audio"]))
        c, edits, n = cer(m["reference_text"], tr["text"])
        loc = cases[cid]["location"]
        r_text = analyze(m["reference_text"], loc, args.method)
        r_stt = analyze(tr["text"], loc, args.method)  # 사용자가 수정하지 않았다고 가정한 최악 조건
        g = sorted(gold[cid]["hazard_codes"])
        row = {
            "case_id": cid, "audio_source": m["source"], "is_real_recording": m["is_real_recording"],
            "reference_text": m["reference_text"], "transcript": tr["text"],
            "cer": c, "char_edits": edits, "ref_chars": n,
            "stt_device": tr["device"], "stt_model": tr["model_size"], "audio_sec": tr["audio_duration_sec"],
            "stt_elapsed_sec": tr["elapsed_sec"], "stt_load_sec": tr["load_sec"],
            "gold_codes": "|".join(g), "codes_from_text": "|".join(codes(r_text)), "codes_from_stt": "|".join(codes(r_stt)),
            "stt_preserves_text_result": codes(r_text) == codes(r_stt),
            "stt_result_matches_gold": codes(r_stt) == g,
            "text_result_matches_gold": codes(r_text) == g,
        }
        rows.append(row)
        full.append({"case_id": cid, "stt": tr, "analysis_text": r_text, "analysis_stt": r_stt})
        print(cid, f"CER={c:.4f}", row["codes_from_stt"], "보존" if row["stt_preserves_text_result"] else "변경")

    with (run_dir / "stt_per_case.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (run_dir / "stt_outputs.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in full), encoding="utf-8")
    tot_e, tot_n = sum(r["char_edits"] for r in rows), sum(r["ref_chars"] for r in rows)
    summary = {
        "method": args.method,
        "n_audio": len(rows),
        "audio_is_real_recording": False,
        "note": "합성 음성(TTS) 기준. 실제 현장 녹음의 정확도를 대표하지 않음.",
        "corpus_cer": tot_e / tot_n,
        "mean_cer": sum(r["cer"] for r in rows) / len(rows),
        "exact_transcripts": sum(r["char_edits"] == 0 for r in rows),
        "stt_preserves_text_result": sum(r["stt_preserves_text_result"] for r in rows),
        "stt_result_matches_gold": sum(r["stt_result_matches_gold"] for r in rows),
        "text_result_matches_gold": sum(r["text_result_matches_gold"] for r in rows),
        "stt_device": rows[0]["stt_device"],
        "stt_model": rows[0]["stt_model"],
        "stt_elapsed_sec_total": sum(r["stt_elapsed_sec"] for r in rows),
        "audio_sec_total": sum(r["audio_sec"] for r in rows),
    }
    (run_dir / "stt_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(run_dir)


if __name__ == "__main__":
    main()

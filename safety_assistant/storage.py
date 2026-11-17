"""신고 기록 저장. 사건마다 records/<사건ID>/ 폴더에 원본·AI 결과·사용자 수정본·사진을 따로 보존합니다.

- AI 결과(ai_result)와 사용자 수정본(user_revisions)은 구분해 저장합니다.
- 사용자 수정은 덮어쓰지 않고 revision 을 추가합니다.
- 시각 기록(created/analyzed/reviewed)으로 처리 시간과 수정 여부를 측정할 수 있습니다.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from .config import path_of

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
ALLOWED_AUDIO_EXT = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_record_id() -> str:
    return datetime.now().strftime("R%Y%m%d-%H%M%S-") + secrets.token_hex(2)


def record_dir(rid: str) -> Path:
    return path_of("records_dir") / rid


def save(rec: dict) -> Path:
    d = record_dir(rec["record_id"])
    d.mkdir(parents=True, exist_ok=True)
    p = d / "record.json"
    tmp = d / "record.json.tmp"
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return p


def load(rid: str) -> dict:
    return json.loads((record_dir(rid) / "record.json").read_text(encoding="utf-8"))


def list_records() -> list[dict]:
    base = path_of("records_dir")
    out = []
    for p in sorted(base.glob("R*/record.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return out


def fingerprint(title: str, location: str, text: str, stt_text: str = "", files: list[bytes] | None = None) -> str:
    """같은 신고 내용인지 판단하는 지문(재시도·새로고침으로 중복 기록이 생기지 않게 함)."""
    h = hashlib.sha256()
    for part in (title, location, text, stt_text):
        h.update((part or "").strip().encode("utf-8") + b"\x00")
    for data in files or []:
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def find_recent_by_fingerprint(fp: str, within_sec: int = 1800) -> dict | None:
    """최근 within_sec 초 안에 같은 지문으로 저장된 기록."""
    limit = datetime.now() - timedelta(seconds=within_sec)
    for r in list_records():
        if r.get("fingerprint") == fp and datetime.fromisoformat(r["timeline"]["created"]) >= limit:
            return r
    return None


def create(title: str, location: str, text: str, fp: str | None = None) -> dict:
    rec = {
        "record_id": new_record_id(),
        "title": title,
        "location": location,
        "fingerprint": fp,
        "input": {"text": text, "stt": None, "stt_edited_text": None},
        "photos": [],
        "audio": None,
        "ai_result": None,          # 가장 최근 분석 결과(AI·규칙·수동 양식) — 수정하지 않음
        "analysis_attempts": [],    # 모든 분석 시도(실패 포함) — 추가만 함
        "user_revisions": [],       # 사용자 확인·수정본 — 추가만 함
        "timeline": {"created": now()},
    }
    save(rec)
    return rec


def record_analysis(rec: dict, result: dict) -> dict:
    """분석 시도를 추가 기록하고 ai_result 를 최신 시도로 바꿉니다(이전 시도는 analysis_attempts 에 남음)."""
    attempts = rec.setdefault("analysis_attempts", [])
    if not attempts and rec.get("ai_result"):  # 이 기능 이전에 저장된 기록
        attempts.append(dict(rec["ai_result"], attempt=1, recorded_at=rec["timeline"].get("analyzed")))
    result = dict(result, attempt=len(attempts) + 1, recorded_at=now())
    attempts.append(result)
    rec["ai_result"] = result
    rec["timeline"]["analyzed"] = result["recorded_at"]
    save(rec)
    return result


def _unique_name(d: Path, name: str) -> Path:
    p = d / name
    i = 1
    while p.exists():
        p = d / f"{Path(name).stem}_{i}{Path(name).suffix}"
        i += 1
    return p


def attach_file(rec: dict, filename: str, data: bytes, kind: str) -> str:
    ext = Path(filename).suffix.lower()
    allowed = ALLOWED_IMAGE_EXT if kind == "photo" else ALLOWED_AUDIO_EXT
    if ext not in allowed:
        raise ValueError(f"허용하지 않는 파일 형식: {ext}")
    sub = record_dir(rec["record_id"]) / ("photos" if kind == "photo" else "audio")
    sub.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in Path(filename).name if ch.isalnum() or ch in "._-") or f"file{ext}"
    p = _unique_name(sub, safe)
    p.write_bytes(data)
    rel = str(p.relative_to(record_dir(rec["record_id"]))).replace("\\", "/")
    if kind == "photo":
        rec["photos"].append({"path": rel, "original_name": filename, "attached_at": now(),
                              "ai_analyzed": False})
    else:
        rec["audio"] = {"path": rel, "original_name": filename, "attached_at": now()}
    save(rec)
    return rel


def add_revision(rec: dict, output: dict, reviewer_note: str = "") -> dict:
    ai_out = (rec.get("ai_result") or {}).get("output")
    rev = {
        "revision": len(rec["user_revisions"]) + 1,
        "saved_at": now(),
        "output": output,
        "edited_from_ai": ai_out is not None and json.dumps(ai_out, sort_keys=True, ensure_ascii=False)
        != json.dumps(output, sort_keys=True, ensure_ascii=False),
        "changed_fields": [k for k in output if ai_out is None or ai_out.get(k) != output.get(k)],
        "based_on_attempt": (rec.get("ai_result") or {}).get("attempt"),
        "based_on_backend": (rec.get("ai_result") or {}).get("backend"),
        "note": reviewer_note,
    }
    rec["user_revisions"].append(rev)
    rec["timeline"]["reviewed"] = rev["saved_at"]
    save(rec)
    return rev


def delete_record_dir_for_tests(rid: str) -> None:
    shutil.rmtree(record_dir(rid), ignore_errors=True)

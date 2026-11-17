"""LLM 구조화 출력 백엔드.

- ollama   : 로컬 실행. 입력이 localhost 밖으로 나가지 않습니다.
- anthropic: 외부 API. 입력이 외부로 전송됩니다. API 키는 환경변수로만 받습니다.
백엔드·모델·온도 등은 config/settings.yaml 에서 읽습니다.
"""
from __future__ import annotations

import hashlib
import json
import os

import requests

from .config import ROOT, load_settings, load_taxonomy
from .schema import output_json_schema


class BackendUnavailable(RuntimeError):
    """백엔드를 쓸 수 없음(서버 미실행, API 키 없음 등)."""


def build_system_prompt(prompt_file: str | None = None) -> tuple[str, str, str]:
    """(프롬프트 본문, 파일 경로, SHA-256)."""
    s = load_settings()
    prompt_file = prompt_file or s["llm"]["prompt_file"]
    path = ROOT / s["paths"]["prompts_dir"] / prompt_file
    template = path.read_text(encoding="utf-8")
    tax = load_taxonomy()
    lines = [f"- {code} [{spec['category']}] {spec['name']}" for code, spec in tax["codes"].items()]
    prompt = template.replace("{TAXONOMY}", "\n".join(lines))
    return prompt, str(path.relative_to(ROOT)), hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def build_user_message(text: str, location: str) -> str:
    return json.dumps({"장소": location or "(입력 없음)", "신고 원문": text}, ensure_ascii=False)


def backend_info(name: str) -> dict:
    cfg = load_settings()["llm"]["backends"][name]
    return {
        "backend": name,
        "model": cfg["model"],
        "execution_location": cfg["execution_location"],
        "data_transfer": cfg["data_transfer"],
        "license": cfg["license"],
    }


def is_available(name: str) -> tuple[bool, str]:
    cfg = load_settings()["llm"]["backends"][name]
    if name == "ollama":
        try:
            r = requests.get(cfg["base_url"] + "/api/tags", timeout=3)
            models = [m["name"] for m in r.json().get("models", [])]
            if cfg["model"] not in models:
                return False, f"Ollama 는 실행 중이나 모델 {cfg['model']} 이 없습니다 (ollama pull {cfg['model']})."
            return True, "사용 가능"
        except Exception as e:  # noqa: BLE001
            return False, f"Ollama 서버에 연결할 수 없습니다: {e}"
    if name == "anthropic":
        if not os.environ.get(cfg["api_key_env"]):
            return False, f"환경변수 {cfg['api_key_env']} 가 없습니다."
        return True, "API 키 있음(호출 성공 여부는 별도)"
    return False, f"알 수 없는 백엔드: {name}"


def call_ollama(system: str, user: str) -> tuple[str, dict]:
    cfg = load_settings()["llm"]["backends"]["ollama"]
    body = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": output_json_schema(),
        "stream": False,
        "think": cfg["think"],
        "options": {"temperature": cfg["temperature"], "num_ctx": cfg["num_ctx"]},
    }
    try:
        r = requests.post(cfg["base_url"] + "/api/chat", json=body, timeout=cfg["timeout_sec"])
    except requests.ConnectionError as e:
        raise BackendUnavailable(f"Ollama 연결 실패: {e}") from e
    r.raise_for_status()
    data = r.json()
    meta = {k: data.get(k) for k in ("total_duration", "load_duration", "prompt_eval_count", "eval_count", "eval_duration")}
    return data["message"]["content"], meta


def call_anthropic(system: str, user: str) -> tuple[str, dict]:
    """Messages API 의 도구 강제 호출로 스키마에 맞는 JSON 을 받습니다. (이 PoC 환경에서는 키가 없어 미실행)"""
    cfg = load_settings()["llm"]["backends"]["anthropic"]
    key = os.environ.get(cfg["api_key_env"])
    if not key:
        raise BackendUnavailable(f"환경변수 {cfg['api_key_env']} 가 없습니다.")
    tool = {"name": "submit_report", "description": "위험요인 신고서 초안 제출", "input_schema": output_json_schema()}
    body = {
        "model": cfg["model"],
        "max_tokens": cfg["max_tokens"],
        "temperature": cfg["temperature"],
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [tool],
        "tool_choice": {"type": "tool", "name": "submit_report"},
    }
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    r = requests.post(cfg["base_url"], headers=headers, json=body, timeout=cfg["timeout_sec"])
    r.raise_for_status()
    data = r.json()
    block = next(b for b in data["content"] if b.get("type") == "tool_use")
    return json.dumps(block["input"], ensure_ascii=False), {"usage": data.get("usage"), "stop_reason": data.get("stop_reason")}


BACKENDS = {"ollama": call_ollama, "anthropic": call_anthropic}

"""LLM 구조화 출력 백엔드.

- ollama   : 로컬 실행. 입력이 localhost 밖으로 나가지 않습니다.
- anthropic: 외부 API. 입력이 외부로 전송됩니다. API 키는 환경변수로만 받습니다.
백엔드·모델·온도 등은 config/settings.yaml 에서 읽습니다.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import queue
import socket
import threading
import time
from urllib.parse import urlsplit

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


class BackendStalled(RuntimeError):
    """백엔드가 응답을 시작하지 않거나 생성 도중 멈춤. diagnostics 에 단계·경과 시간·받은 토큰 수를 담습니다."""

    def __init__(self, msg: str, diagnostics: dict):
        super().__init__(msg)
        self.diagnostics = diagnostics


def ollama_loaded_models() -> list[dict]:
    """현재 메모리에 올라와 있는 모델(/api/ps). 실패하면 빈 목록."""
    cfg = load_settings()["llm"]["backends"]["ollama"]
    try:
        r = requests.get(cfg["base_url"] + "/api/ps", timeout=3)
        return [{"name": m.get("name"), "size_vram": m.get("size_vram"), "size": m.get("size"),
                 "context_length": m.get("context_length"), "expires_at": m.get("expires_at")}
                for m in r.json().get("models", [])]
    except Exception:  # noqa: BLE001
        return []


def ollama_warmup() -> dict:
    """모델을 미리 GPU 에 올려 둡니다(프롬프트 없이 호출하면 Ollama 는 모델만 불러옵니다)."""
    cfg = load_settings()["llm"]["backends"]["ollama"]
    t0 = time.perf_counter()
    r = requests.post(cfg["base_url"] + "/api/generate",
                      json={"model": cfg["model"], "keep_alive": cfg["keep_alive"], "options": {"num_ctx": cfg["num_ctx"]}},
                      timeout=(cfg["connect_timeout_sec"], cfg["first_token_timeout_sec"]))
    r.raise_for_status()
    return {"elapsed_sec": time.perf_counter() - t0, "loaded": ollama_loaded_models()}


def call_ollama(system: str, user: str) -> tuple[str, dict]:
    """스트리밍으로 호출하고 단계별로 멈춤을 감지합니다.

    - 첫 조각까지(모델 로딩 + 프롬프트 처리): first_token_timeout_sec
    - 조각 사이 간격: stall_timeout_sec
    - 전체: max_total_sec
    멈추면 연결을 끊어(Ollama 는 연결이 끊기면 생성을 취소함) GPU 를 붙잡고 있지 않게 합니다.
    """
    cfg = load_settings()["llm"]["backends"]["ollama"]
    body = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": output_json_schema(),
        "stream": True,
        "think": cfg["think"],
        "keep_alive": cfg["keep_alive"],
        "options": {"temperature": cfg["temperature"], "num_ctx": cfg["num_ctx"], "num_predict": cfg["num_predict"]},
    }
    was_loaded = any(m["name"] == cfg["model"] for m in ollama_loaded_models())
    u = urlsplit(cfg["base_url"])
    t0 = time.perf_counter()
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=cfg["connect_timeout_sec"])
    try:
        conn.connect()
        conn.sock.settimeout(None)  # 응답 대기는 아래 감시 루프가 맡음
        conn.request("POST", "/api/chat", body=json.dumps(body).encode("utf-8"),
                     headers={"Content-Type": "application/json"})
    except OSError as e:
        conn.close()
        raise BackendUnavailable(f"Ollama 연결 실패: {e}") from e

    def abort() -> None:
        # Windows 에서는 shutdown() 이 다른 스레드의 대기 중 recv 를 깨우지 못하므로 OS 핸들을 직접 닫습니다.
        # 연결이 끊기면 Ollama 는 진행 중인 생성을 취소합니다(서버 로그 'cancel task').
        try:
            if conn.sock is not None:
                socket.close(conn.sock.detach())
        except OSError:
            pass

    q: queue.Queue = queue.Queue()

    def reader() -> None:
        # 응답 헤더 대기(getresponse)도 감시 대상이어야 합니다. Ollama 는 모델 로딩·프롬프트 처리가
        # 끝난 뒤에야 헤더를 보내므로, 여기서 멈추면 '응답 시작 전 멈춤'으로 판정됩니다.
        try:
            resp = conn.getresponse()
            if resp.status >= 400:
                q.put(("error", RuntimeError(f"Ollama HTTP {resp.status}: {resp.read(300).decode('utf-8', 'replace')}")))
                return
            while True:
                line = resp.readline()
                if not line:
                    q.put(("eof", None))
                    return
                if line.strip():
                    q.put(("data", line))
        except Exception as e:  # noqa: BLE001  (abort() 로 연결을 끊으면 여기로 옴)
            q.put(("error", e))

    threading.Thread(target=reader, daemon=True).start()
    parts: list[str] = []
    first_at = None
    final: dict | None = None
    try:
        while True:
            elapsed = time.perf_counter() - t0
            if elapsed > cfg["max_total_sec"]:
                raise BackendStalled("전체 제한 시간 초과", {"phase": "generation", "elapsed_sec": elapsed, "chunks": len(parts)})
            wait = cfg["first_token_timeout_sec"] - elapsed if first_at is None else cfg["stall_timeout_sec"]
            wait = min(wait, cfg["max_total_sec"] - elapsed)
            try:
                kind, val = q.get(timeout=max(wait, 0.01))
            except queue.Empty:
                phase = "load_or_prompt" if first_at is None else "generation"
                raise BackendStalled(
                    "응답 시작 전 멈춤(모델 로딩/프롬프트 처리 단계)" if first_at is None else "생성 도중 멈춤",
                    {"phase": phase, "elapsed_sec": time.perf_counter() - t0, "chunks": len(parts),
                     "model_was_loaded": was_loaded}) from None
            if kind == "error":
                raise RuntimeError(f"Ollama 스트림 오류: {val}")
            if kind == "eof":
                break
            d = json.loads(val)
            if d.get("error"):
                raise RuntimeError(f"Ollama 오류: {d['error']}")
            if first_at is None:
                first_at = time.perf_counter() - t0
            if d.get("done"):
                final = d
                break
            parts.append(d.get("message", {}).get("content", ""))
    finally:
        abort()  # 연결은 재사용하지 않으므로 항상 끊습니다(대기 중인 읽기 스레드도 즉시 풀림).
        conn.close()
    if final is None:
        raise RuntimeError("Ollama 응답이 완료 표시 없이 끝났습니다.")
    ns = 1e9
    meta = {
        "streaming": True,
        "model_was_loaded_before_call": was_loaded,
        "wall_sec": time.perf_counter() - t0,
        "first_chunk_sec": first_at,
        "load_sec": (final.get("load_duration") or 0) / ns,
        "prompt_eval_count": final.get("prompt_eval_count"),
        "prompt_eval_sec": (final.get("prompt_eval_duration") or 0) / ns,
        "eval_count": final.get("eval_count"),
        "eval_sec": (final.get("eval_duration") or 0) / ns,
        "done_reason": final.get("done_reason"),
        "num_ctx": cfg["num_ctx"],
        "num_predict": cfg["num_predict"],
    }
    if final.get("done_reason") == "length":
        raise RuntimeError(f"출력이 num_predict({cfg['num_predict']}) 토큰 상한에 도달해 잘렸습니다.")
    return "".join(parts), meta


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

"""음성 → 텍스트(STT). faster-whisper(MIT) + Whisper 가중치(MIT)를 로컬에서 실행합니다.

- 오디오는 이 PC 밖으로 나가지 않습니다(최초 1회 모델 가중치만 Hugging Face 에서 내려받음).
- device=auto: CUDA 를 쓸 수 있으면 CUDA, 아니면 CPU(int8).
- STT 결과는 그대로 쓰지 않고, 사용자가 확인·수정한 텍스트가 분석 엔진에 들어갑니다.

2026-11-17 장애 원인(재현됨): 이 PC 에는 cuBLAS(cublas64_12.dll)가 없어 CUDA 추론이 실패합니다.
예전 코드는 호출할 때마다 CUDA 모델로 다시 시도했는데, 같은 프로세스에서 두 번째 CUDA 시도가
예외 없이 멈췄습니다(화면의 '전사 중' 무한 대기, 평가 스크립트 정지). 지금은
(1) CUDA 사용 전 cuBLAS DLL 을 먼저 확인하고, (2) 장치 결정을 프로세스당 한 번만 하며,
(3) 화면에서는 별도 프로세스로 실행해 제한 시간을 넘으면 강제 종료합니다.
"""
from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
import wave
from functools import lru_cache
from pathlib import Path

from .config import ROOT, load_settings

_CHOSEN: dict = {}  # 프로세스당 한 번 결정한 (모델, device, compute_type, 결정 근거)


def cuda_libs_ok() -> tuple[bool, str]:
    """CTranslate2 가 CUDA 추론에 쓰는 cuBLAS 를 불러올 수 있는지 미리 확인합니다."""
    if sys.platform != "win32":
        return True, "Windows 외 환경: 확인 생략"
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() < 1:
            return False, "CUDA 장치 없음"
        try:
            import os

            os.add_dll_directory(str(Path(ctranslate2.__file__).parent))
        except Exception:  # noqa: BLE001
            pass
        ctypes.WinDLL("cublas64_12.dll")
        return True, "cublas64_12.dll 로드 가능"
    except OSError as e:
        return False, f"cublas64_12.dll 을 불러올 수 없음: {e}"
    except Exception as e:  # noqa: BLE001
        return False, f"CUDA 확인 실패: {e}"


@lru_cache(maxsize=2)
def _load_model(model_size: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_size, device=device, compute_type=compute_type, download_root=str(ROOT / "models"))


def get_model():
    """(모델, 실제 device, compute_type). 결정은 프로세스당 한 번만 합니다."""
    if _CHOSEN:
        return _CHOSEN["model"], _CHOSEN["device"], _CHOSEN["compute_type"]
    cfg = load_settings()["stt"]
    tries, reasons = [], []
    if cfg["device"] in ("auto", "cuda"):
        ok, why = cuda_libs_ok()
        reasons.append(f"cuda: {why}")
        if ok:
            tries.append(("cuda", cfg["compute_type_cuda"]))
    if cfg["device"] in ("auto", "cpu"):
        tries.append(("cpu", cfg["compute_type_cpu"]))
    last = None
    for dev, ct in tries:
        try:
            m = _load_model(cfg["model_size"], dev, ct)
            if dev == "cuda":  # CUDA 는 실제 추론 때 실패할 수 있으므로 짧은 무음으로 확인
                import numpy as np

                list(m.transcribe(np.zeros(16000, dtype="float32"), language=cfg["language"])[0])
            _CHOSEN.update(model=m, device=dev, compute_type=ct, reasons=reasons)
            return m, dev, ct
        except Exception as e:  # noqa: BLE001
            last = e
            reasons.append(f"{dev}: 실패 {e}")
            if dev == "cuda":
                # CUDA 가 한 번 실패하면 이 프로세스에서는 다시 시도하지 않습니다(재시도 시 멈춤 현상).
                continue
    raise RuntimeError(f"STT 모델을 불러오지 못했습니다: {last} ({reasons})")


def transcribe(audio_path: str) -> dict:
    cfg = load_settings()["stt"]
    t0 = time.perf_counter()
    model, dev, ct = get_model()
    t_load = time.perf_counter() - t0
    segments, info = model.transcribe(audio_path, language=cfg["language"], beam_size=cfg["beam_size"])
    segs = [{"start": s.start, "end": s.end, "text": s.text, "avg_logprob": s.avg_logprob,
             "no_speech_prob": s.no_speech_prob} for s in segments]
    return {
        "text": "".join(s["text"] for s in segs).strip(),
        "segments": segs,  # 신뢰도 관련 원시값(반올림하지 않음)
        "engine": cfg["engine"],
        "model_size": cfg["model_size"],
        "device": dev,
        "compute_type": ct,
        "device_decision": _CHOSEN.get("reasons"),
        "language": info.language,
        "language_probability": info.language_probability,
        "audio_duration_sec": info.duration,
        "load_sec": t_load,
        "elapsed_sec": time.perf_counter() - t0,
    }


def audio_seconds(path: str) -> float | None:
    """WAV 길이(초). WAV 가 아니면 None."""
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:  # noqa: BLE001
        return None


def transcribe_isolated(audio_path: str) -> dict:
    """별도 프로세스에서 전사합니다. 제한 시간을 넘기면 프로세스를 종료하고 TimeoutError 를 냅니다.

    네이티브 라이브러리(CTranslate2)가 멈추면 같은 프로세스 안에서는 중단할 수 없으므로,
    화면이 '전사 중'에 영원히 머무르지 않도록 프로세스 단위로 격리합니다.
    """
    cfg = load_settings()["stt"]
    dur = audio_seconds(audio_path)
    limit = max(cfg["min_timeout_sec"], (dur or 0) * cfg["timeout_per_audio_sec"])
    t0 = time.perf_counter()
    try:
        p = subprocess.run([sys.executable, "-m", "safety_assistant.stt", audio_path], cwd=str(ROOT),
                           capture_output=True, timeout=limit, env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"STT 가 {limit:.0f}초 안에 끝나지 않아 중단했습니다(오디오 {dur or '?'}초).") from None
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"STT 프로세스 실패: {err[-1] if err else p.returncode}")
    res = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])
    res["isolated_process"] = True
    res["wall_sec_including_process_start"] = time.perf_counter() - t0
    res["timeout_limit_sec"] = limit
    return res


if __name__ == "__main__":
    print(json.dumps(transcribe(sys.argv[1]), ensure_ascii=False))

"""STT 시험용 오디오 생성 — Windows 내장 TTS(한국어 음성)로 '합성'합니다.

주의: 실제 사람의 녹음이 아닙니다. 발음이 또렷하고 소음이 없어 실제 현장 녹음보다 쉬운 조건입니다.
이 오디오로는 'STT 입력 경로가 동작하는지'와 '합성 음성 조건의 전사 정확도'만 확인할 수 있습니다.

사용: python scripts/make_test_audio.py  (Windows 전용, PowerShell System.Speech 사용)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from safety_assistant.config import path_of  # noqa: E402

# 시험할 사례(단순·복합·모호·부정문을 섞음)
CASE_IDS = ["C001", "C015", "C021", "C033", "C048", "C063", "C071", "C081", "C087", "C093"]


def main() -> None:
    out_dir = path_of("audio_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = {json.loads(l)["case_id"]: json.loads(l) for l in path_of("cases").read_text(encoding="utf-8").splitlines() if l.strip()}
    manifest = []
    for cid in CASE_IDS:
        text = cases[cid]["text"]
        wav = out_dir / f"{cid}.wav"
        txt = out_dir / f"{cid}.txt"
        txt.write_text(text, encoding="utf-8")
        ps = (
            "Add-Type -AssemblyName System.Speech;"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$v=$s.GetInstalledVoices()|?{$_.VoiceInfo.Culture.Name -eq 'ko-KR'}|select -First 1;"
            "if(-not $v){throw 'ko-KR voice not installed'};"
            "$s.SelectVoice($v.VoiceInfo.Name);"
            f"$s.SetOutputToWaveFile('{wav}');"
            f"$s.Speak([IO.File]::ReadAllText('{txt}',[Text.Encoding]::UTF8));$s.Dispose();"
            "$v.VoiceInfo.Name"
        )
        voice = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, check=True).stdout.strip()
        txt.unlink()
        manifest.append({"case_id": cid, "audio": f"{cid}.wav", "reference_text": text, "source": f"합성 음성(Windows TTS: {voice})",
                         "is_real_recording": False})
        print(cid, wav.name, voice)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

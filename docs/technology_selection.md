# 기술 후보 검토와 선정 (2026-11-17 확인)

> 가격·라이선스·지원 언어는 바뀝니다. 아래는 이 날짜에 이 PC에서 확인한 내용입니다.
> "확인 방법" 열에 무엇으로 확인했는지 적었습니다. 확인하지 못한 항목은 "미확인"으로 적었습니다.

## 1. 먼저 거른 조건 (통과/탈락)

점수 비교 전에 두 조건으로 먼저 걸렀습니다(교안 「실행 위치와 라이선스 먼저 확인하기」).

| 조건 | 기준 |
|---|---|
| 자료가 어디까지 나가는가 | 실제 현장 도입을 가정하면 신고 원문에는 사업장명·위치·인원 정보가 섞일 수 있으므로 **로컬 실행 후보를 기본값**으로 둠. 외부 API 는 "외부 전송 허용 시"에만 쓰는 선택지로 남김 |
| 라이선스 | 상업적 재사용 가능성이 있으므로 **상업 사용이 허용되는 라이선스**만 기본 구성에 넣음 |

## 2. LLM 후보

| 후보 | 성격 | 실행 위치 | 데이터 전송 | 이용조건 | 확인 방법 | 이번 PoC 실행 |
|---|---|---|---|---|---|---|
| 규칙 기반 분류기 | 키워드 규칙 (AI 아님) | 로컬 | 없음 | 자체 코드 | — | **실행함** (기준선) |
| Qwen3.5 4B (Ollama, Q4_K_M) | 범용 소형 LLM, 다국어 | 로컬 GPU (RTX 2070 SUPER 8GB) | 없음 (localhost) | Apache-2.0 | `ollama show qwen3.5:4b --license` | **실행함** |
| Claude Haiku 5.5 (Anthropic API) | 범용 대형 LLM | 외부 클라우드 | 원문·장소가 외부 전송 | Anthropic 상용 약관 | 코드만 구현 | **실행 안 함** (API 키 없음) |
| 사람 수작업 | 현재 업무 방식 | — | — | — | — | **측정 안 함** (템플릿만 제공) |

- 로컬 LLM 은 이 PC에 이미 설치되어 있던 `qwen3.5:4b` 하나만 시험했습니다. 다른 로컬 모델(예: 다른 계열의 4~8B 모델)은 내려받지 않았습니다 — 다음 단계 후보입니다.
- 외부 API 경로(`safety_assistant/llm_engine.py: call_anthropic`)는 구현만 되어 있고 **한 번도 호출해 보지 않았습니다.** 실행 결과를 적지 않았습니다.
- Qwen3.5 4B 는 "thinking" 기능이 있으나 응답 시간 때문에 `think: false` 로 고정했습니다(설정값).

## 3. STT 후보

| 후보 | 한국어 | 로컬 실행 | 하드웨어 | 라이선스 | 선정 |
|---|---|---|---|---|---|
| faster-whisper (CTranslate2) + Whisper `small` | 지원(Whisper 다국어 모델) | 가능 | CPU int8 로도 동작, CUDA 는 cuBLAS/cuDNN 필요 | 패키지 MIT, 가중치 MIT | **선정** |
| openai-whisper (PyTorch) | 지원 | 가능 | PyTorch 설치 필요(용량 큼), 시스템 ffmpeg 필요 | MIT | 탈락: 설치 부담 |
| 외부 STT API | 지원 | 불가 | — | 상용 약관 | 탈락: 음성을 외부로 보냄 |

- faster-whisper 는 PyAV 를 통해 오디오를 디코딩하므로 시스템 ffmpeg 설치가 필요 없습니다. PyAV(BSD-3)는 FFmpeg 라이브러리(LGPL)를 동봉합니다 — 제품 배포 시 LGPL 고지 확인 필요.
- 모델 크기(`stt.model_size`)와 장치(`stt.device`)는 설정값입니다.

## 4. UI 후보

| 후보 | 장점 | 단점 | 선정 |
|---|---|---|---|
| Streamlit (Apache-2.0) | 파이썬만으로 입력폼·파일 업로드·녹음(`st.audio_input`)·이미지 미리보기 제공, 빠른 구현 | 다중 사용자·권한·세밀한 화면 제어에 약함 | **선정** (PoC 목적: 동작 확인 우선) |
| Gradio | 비슷하게 빠름 | 여러 화면(신고/검토/결과) 흐름 구성이 Streamlit 보다 번거로움 | 미선정 |
| FastAPI + 프런트엔드 | 제품화에 유리 | 구현량이 많음 | 다음 단계 후보 |

## 5. 설치된 파이썬 패키지 라이선스 (설치 패키지 메타데이터에서 확인)

| 패키지 | 버전 | 라이선스 |
|---|---|---|
| streamlit | 1.67.0 | Apache-2.0 |
| faster-whisper | 1.2.1 | MIT |
| ctranslate2 | 4.9.0 | MIT |
| av (PyAV) | 18.1.0 | BSD-3-Clause (FFmpeg LGPL 동봉) |
| PyYAML | 6.0.3 | MIT |
| requests | 2.34.2 | Apache-2.0 |
| numpy | 2.4.6 | BSD-3-Clause 외 |
| huggingface-hub | 2.4.0 | Apache-2.0 |
| onnxruntime | 1.32.0 | MIT |

확인 명령: `python -c "from importlib.metadata import metadata; print(metadata('streamlit')['License-Expression'])"`

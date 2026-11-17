"""Safety Inspection Assistant — 교육용 웹 UI (Streamlit).

실행: streamlit run app.py
화면: A. 위험요인 신고 / B. AI 보고서 검토 / C. 결과 확인

신고 원본(텍스트·STT·사진·음성)은 분석 전에 먼저 저장합니다. AI 분석이 실패해도 같은 기록에서
다시 시도하거나 규칙 기반 분석·수동 작성으로 이어갈 수 있으며, 재시도는 새 신고를 만들지 않습니다.
"""
from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from safety_assistant import llm_engine, report, storage
from safety_assistant.config import load_settings, load_taxonomy
from safety_assistant.engine import analyze, manual_result

st.set_page_config(page_title="Safety Inspection Assistant (교육용 PoC)", layout="wide")
S = load_settings()
TAX = load_taxonomy()
CODES = TAX["codes"]
LABELS = {"ollama": "로컬 LLM (Ollama)", "rule": "규칙 기반 (AI 아님, 로컬)",
          "anthropic": "외부 API (Anthropic)", "manual": "수동 작성 (분석 없음)"}


@st.cache_data(ttl=20, show_spinner=False)
def backend_status(name: str) -> tuple[bool, str]:
    return llm_engine.is_available(name)


def available_methods() -> list[str]:
    """지금 쓸 수 있는 분석 방식. 외부 API 는 키가 있을 때만 포함합니다."""
    opts = []
    if backend_status("ollama")[0]:
        opts.append("ollama")
    opts.append("rule")
    if backend_status("anthropic")[0]:
        opts.append("anthropic")
    return opts


# ---------------- 사이드바 ----------------
st.sidebar.title("Safety Inspection Assistant")
st.sidebar.caption("교육용 PoC · 가상 데이터 전용 · 법적 판정 아님")
page = st.sidebar.radio("화면", ["A. 위험요인 신고", "B. AI 보고서 검토", "C. 결과 확인"], key="page")

opts = available_methods()
default = S["llm"]["default_backend"] if S["llm"]["default_backend"] in opts else "rule"
backend = st.sidebar.selectbox("분석 방식", opts, index=opts.index(default), format_func=lambda b: LABELS[b])

ok_o, msg_o = backend_status("ollama")
if not ok_o:
    st.sidebar.error(f"로컬 LLM 사용 불가: {msg_o}")
    st.sidebar.info("AI 없이도 규칙 기반 분석·수동 작성·저장·보고서 출력은 가능합니다.")
if backend == "ollama":
    info = llm_engine.backend_info("ollama")
    st.sidebar.write(f"모델: `{info['model']}` · {info['execution_location']} · 데이터 전송: {info['data_transfer']}")
    loaded = [m for m in llm_engine.ollama_loaded_models() if m["name"] == info["model"]]
    if loaded:
        st.sidebar.success(f"모델이 메모리에 올라와 있음 (VRAM {loaded[0]['size_vram'] / 2**30:.1f} GiB)")
    else:
        st.sidebar.warning("모델이 아직 메모리에 없음 — 첫 분석에 약 15초가 더 걸립니다.")
        if st.sidebar.button("모델 미리 불러오기"):
            with st.sidebar.status("모델 불러오는 중…"):
                try:
                    w = llm_engine.ollama_warmup()
                    st.sidebar.success(f"완료 ({w['elapsed_sec']:.1f}초)")
                except Exception as e:  # noqa: BLE001
                    st.sidebar.error(f"불러오기 실패: {e}")
if backend == "anthropic":
    st.sidebar.warning("외부 API 선택 시 신고 원문과 장소가 외부 서버로 전송됩니다(유료 API).")
ok_a, msg_a = backend_status("anthropic")
if not ok_a:
    st.sidebar.caption(
        f"외부 API (Anthropic): **비활성** — {msg_a} Claude 채팅 구독(Team 등)은 API 키·크레딧이 아니므로 "
        "이 앱에서 쓸 수 없습니다. 이 PoC는 외부 API 없이 모두 동작합니다.")


def go(p: str) -> None:
    st.session_state["page"] = p
    st.session_state.pop("last_rev", None)


def run_analysis(rec: dict, method: str) -> dict:
    """같은 기록에 분석 시도를 추가합니다(새 신고를 만들지 않음)."""
    text = rec["input"].get("analyzed_text") or rec["input"].get("text") or ""
    if method == "manual":
        res = manual_result(text, rec["location"])
    else:
        with st.spinner(f"{LABELS[method]} 분석 중…"):
            res = analyze(text, rec["location"], method)
    return storage.record_analysis(rec, res)


def describe_error(ai: dict) -> str:
    d = ai.get("error_diagnostics") or {}
    extra = ""
    if d:
        extra = f" (단계: {d.get('phase')}, 경과 {d.get('elapsed_sec', 0):.1f}초, 받은 조각 {d.get('chunks')}개" \
                f"{', 모델 미로딩 상태였음' if d.get('model_was_loaded') is False else ''})"
    return f"{ai.get('error')}{extra}"


def fallback_controls(rec: dict, prefix: str) -> None:
    """분석 실패 시: 다시 시도 / 규칙 기반 / 수동 작성 — 모두 같은 기록에서."""
    st.write("원본 신고·사진·음성은 저장되어 있습니다. 다음 중 하나로 이어서 진행하세요.")
    cols = st.columns(3)
    choice = None
    if backend_status("ollama")[0] and cols[0].button("로컬 LLM 다시 시도", key=prefix + "retry"):
        choice = "ollama"
    if cols[1].button("규칙 기반으로 분석", key=prefix + "rule"):
        choice = "rule"
    if cols[2].button("수동 작성", key=prefix + "manual"):
        choice = "manual"
    if choice:
        run_analysis(rec, choice)
        st.rerun()


# ---------------- A. 신고 ----------------
def reset_form() -> None:
    for k in ("f_title", "f_location", "f_text", "stt", "stt_edit", "audio_bytes", "draft_rid", "draft_fp", "dup_notice"):
        st.session_state.pop(k, None)
    st.session_state["form_gen"] = st.session_state.get("form_gen", 0) + 1  # 파일 위젯 초기화


def page_report() -> None:
    st.header("A. 위험요인 신고")
    gen = st.session_state.get("form_gen", 0)
    c1, c2 = st.columns(2)
    title = c1.text_input("사건명", placeholder="예: 2라인 배전반 앞 적치물", key="f_title")
    location = c2.text_input("장소", placeholder="예: 조립2라인", key="f_location")
    text = st.text_area("위험 상황 설명 (텍스트)", height=120, placeholder="본 그대로 적어 주세요.", key="f_text")

    st.subheader("음성 입력 (선택)")
    st.caption("음성 → STT(로컬 faster-whisper, 별도 프로세스) → 전사 결과 확인·수정 → 분석. "
               "STT가 실패하거나 제한 시간을 넘기면 위 텍스트 칸에 직접 입력하세요.")
    a1, a2 = st.columns(2)
    rec_audio = a1.audio_input("마이크로 녹음", key=f"mic_{gen}")
    up_audio = a2.file_uploader("또는 오디오 파일", type=[e[1:] for e in storage.ALLOWED_AUDIO_EXT], key=f"aud_{gen}")
    audio = rec_audio or up_audio
    if audio is not None:
        name = getattr(audio, "name", None) or "recording.wav"
        if st.button("음성 전사 실행"):
            with st.spinner("전사 중… (CPU 기준 보통 10~20초, 제한 시간을 넘기면 자동 중단)"):
                try:
                    from safety_assistant import stt

                    with tempfile.NamedTemporaryFile(suffix=Path(name).suffix or ".wav", delete=False) as tf:
                        tf.write(audio.getvalue())
                    res = stt.transcribe_isolated(tf.name)
                    st.session_state["stt"] = res
                    st.session_state["stt_edit"] = res["text"]
                    st.session_state["audio_bytes"] = (name, audio.getvalue())
                except Exception as e:  # noqa: BLE001
                    st.error(f"STT 실패: {e} — 텍스트로 직접 입력해 신고를 완료할 수 있습니다.")
    if st.session_state.get("stt"):
        r = st.session_state["stt"]
        st.text_area("STT 전사 결과 — 확인 후 필요하면 수정하세요", key="stt_edit", height=90)
        st.caption(f"STT 원문: {r['text']} · 모델 {r['model_size']} · {r['device']}/{r['compute_type']} · "
                   f"전사 {r['elapsed_sec']:.1f}초 (프로세스 포함 {r.get('wall_sec_including_process_start', 0):.1f}초)")
        if st.button("전사 결과 지우기"):
            for k in ("stt", "stt_edit", "audio_bytes"):
                st.session_state.pop(k, None)
            st.rerun()

    st.subheader("현장 사진 첨부 (선택)")
    st.caption("사진은 참고자료로만 보존합니다. AI는 사진을 분석하지 않습니다.")
    photos = st.file_uploader("사진", type=[e[1:] for e in storage.ALLOWED_IMAGE_EXT], accept_multiple_files=True,
                              key=f"pho_{gen}")
    if photos:
        st.image([p.getvalue() for p in photos], width=180, caption=[p.name for p in photos])

    stt_text = st.session_state.get("stt_edit", "").strip() if st.session_state.get("stt") else ""
    combined = "\n".join(t for t in [text.strip(), stt_text] if t)
    files = [p.getvalue() for p in photos or []]
    if st.session_state.get("audio_bytes"):
        files.append(st.session_state["audio_bytes"][1])
    fp = storage.fingerprint(title, location, text, stt_text, files)

    if st.button(f"신고 저장 + 분석 ({LABELS[backend]})", type="primary"):
        if not combined and not photos:
            st.warning("설명(텍스트 또는 음성)이나 사진 중 하나는 있어야 합니다.")
            return
        rec = None
        if st.session_state.get("draft_fp") == fp and st.session_state.get("draft_rid"):
            rec = storage.load(st.session_state["draft_rid"])  # 같은 입력으로 다시 누름 → 같은 기록
            st.session_state["dup_notice"] = f"같은 내용이 이미 {rec['record_id']} 로 저장되어 있어 새 신고를 만들지 않고 다시 분석했습니다."
        elif (old := storage.find_recent_by_fingerprint(fp)) is not None:
            rec = old  # 새로고침 등으로 세션이 바뀐 경우
            st.session_state["dup_notice"] = f"최근 30분 안에 같은 내용이 {rec['record_id']} 로 저장되어 있어 그 기록을 다시 분석했습니다."
        else:
            st.session_state.pop("dup_notice", None)
            rec = storage.create(title or "(제목 없음)", location, text, fp)
            if st.session_state.get("stt"):
                rec["input"]["stt"] = st.session_state["stt"]
                rec["input"]["stt_edited_text"] = stt_text
                fname, data = st.session_state["audio_bytes"]
                storage.attach_file(rec, fname, data, "audio")
            for p in photos or []:
                storage.attach_file(rec, p.name, p.getvalue(), "photo")
            rec["input"]["analyzed_text"] = combined
            rec["timeline"]["saved_before_analysis"] = storage.now()
            storage.save(rec)  # 분석 전에 원본을 먼저 저장
        st.session_state["draft_rid"], st.session_state["draft_fp"] = rec["record_id"], fp
        st.session_state["current"] = rec["record_id"]
        run_analysis(rec, backend)
        st.rerun()

    # 버튼 안에 버튼을 두면 Streamlit 재실행 때 사라지므로 결과·후속 버튼은 바깥에 둡니다.
    rid = st.session_state.get("draft_rid")
    if rid:
        rec = storage.load(rid)
        ai = rec.get("ai_result") or {}
        st.divider()
        if st.session_state.get("dup_notice"):
            st.info(st.session_state["dup_notice"])
        st.write(f"저장된 신고: **{rid}** · 분석 시도 {len(rec.get('analysis_attempts') or [])}회")
        if ai.get("error"):
            st.error(f"{LABELS.get(ai.get('backend'), ai.get('backend'))} 분석 실패: {describe_error(ai)}")
            fallback_controls(rec, "a_")
        elif ai:
            kind = "실제 AI 추론" if ai.get("is_ai_inference") else LABELS.get(ai.get("backend"), ai.get("backend"))
            st.success(f"분석 완료 — {kind} · {ai.get('elapsed_sec', 0):.1f}초")
        b1, b2 = st.columns(2)
        b1.button("검토 화면으로 이동", on_click=go, args=("B. AI 보고서 검토",), type="primary")
        b2.button("새 신고 작성", on_click=reset_form)


# ---------------- B. 검토 ----------------
def pick_record(label: str) -> dict | None:
    recs = storage.list_records()
    if not recs:
        st.info("저장된 신고가 없습니다.")
        return None
    ids = [r["record_id"] for r in recs]
    cur = st.session_state.get("current")
    idx = ids.index(cur) if cur in ids else 0
    rid = st.selectbox(label, ids, index=idx,
                       format_func=lambda i: f"{i} · {next(r['title'] for r in recs if r['record_id'] == i)}")
    st.session_state["current"] = rid
    return storage.load(rid)


def page_review() -> None:
    st.header("B. AI 보고서 검토")
    rec = pick_record("검토할 신고")
    if not rec:
        return
    ai = rec.get("ai_result") or {}
    info = ai.get("info") or {}
    if ai.get("is_ai_inference"):
        st.success(f"실제 AI 추론 결과 · 모델 `{info.get('model')}` · {info.get('execution_location')} · {ai.get('elapsed_sec', 0):.1f}초")
    elif ai.get("error"):
        st.error(f"분석 실패: {describe_error(ai)} — 아래에서 다시 분석하거나 직접 작성하세요.")
    elif info.get("backend") == "rule":
        st.info("규칙 기반 결과입니다 (AI 추론 아님).")
    elif info.get("backend") == "manual":
        st.info("수동 작성 양식입니다 (분석 없음).")
    else:
        st.warning("분석 결과가 없습니다. " + (ai.get("note") or ""))

    attempts = rec.get("analysis_attempts") or []
    with st.expander(f"분석 다시 실행 / 다른 방식 선택 (지금까지 {len(attempts)}회 시도)", expanded=bool(ai.get("error") or not ai)):
        st.caption("같은 신고 기록에 분석 시도가 추가됩니다. 이전 결과와 사용자 수정본은 지워지지 않습니다.")
        if attempts:
            st.dataframe([{"시도": a.get("attempt"), "방식": LABELS.get(a.get("backend"), a.get("backend")),
                           "AI 추론": a.get("is_ai_inference"), "오류": a.get("error"),
                           "시간(초)": a.get("elapsed_sec"), "기록 시각": a.get("recorded_at")} for a in attempts],
                         hide_index=True, use_container_width=True)
        m_opts = available_methods() + ["manual"]
        m = st.selectbox("방식", m_opts, format_func=lambda b: LABELS[b], key="re_method")
        if st.button("이 방식으로 다시 분석"):
            run_analysis(rec, m)
            st.session_state.pop("last_rev", None)
            st.rerun()

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**신고 원문**")
        st.write(rec["input"].get("text") or "(텍스트 없음)")
        stt = rec["input"].get("stt")
        st.markdown("**STT 결과**")
        if stt:
            st.write(f"전사 원문: {stt['text']}")
            st.write(f"사용자 수정 전사문: {rec['input'].get('stt_edited_text')}")
        else:
            st.caption("음성 입력 없음")
        if rec.get("photos"):
            st.markdown("**첨부 사진 (AI 미분석)**")
            d = storage.record_dir(rec["record_id"])
            st.image([str(d / p["path"]) for p in rec["photos"]], width=160)
    with c2:
        chk = ai.get("checks") or {}
        if chk.get("suspected_fabrication"):
            st.error(f"원문에 없는 내용이 들어갔을 수 있습니다(자동 검사): {chk.get('fabrication_terms')} {chk.get('location_added')}")
        if chk.get("evidence_not_in_source"):
            st.warning(f"원문에서 찾을 수 없는 근거 인용: {chk['evidence_not_in_source']}")
        if ai.get("problems"):
            st.warning(f"출력 형식 문제: {ai['problems']}")
        if ai.get("is_ai_inference"):
            st.caption("AI가 정리한 설명은 원문과 다를 수 있습니다(평가에서 100건 중 18건 사실 변경 확인). 왼쪽 원문과 대조하세요.")

    revs = rec.get("user_revisions") or []
    cur_attempt = ai.get("attempt")
    same = revs and revs[-1].get("based_on_attempt") == cur_attempt
    if revs and not same:
        st.info(f"최근 수정본(수정본 {revs[-1]['revision']})은 이전 분석 결과를 바탕으로 했습니다. 아래 양식은 최신 분석 결과로 채웠습니다.")
    base = revs[-1]["output"] if same else (ai.get("output") or {})
    k = rec["record_id"] + f"_{len(revs)}_{cur_attempt}"
    st.subheader("확인 및 수정")
    with st.form("review_" + k):
        desc = st.text_area("정리된 현장 설명", base.get("cleaned_description", ""), height=90)
        cur_codes = [h["code"] for h in base.get("observed_hazards", []) if h.get("code") in CODES]
        codes = st.multiselect("위험요인", list(CODES), default=cur_codes,
                               format_func=lambda c: f"{CODES[c]['name']} [{CODES[c]['category']}]")
        for h in base.get("observed_hazards", []):
            st.caption(f"· {h.get('name')} — 근거: “{h.get('evidence')}” / {h.get('description')}")
        cats = st.multiselect("위험유형", TAX["categories"], default=[c for c in base.get("hazard_category", []) if c in TAX["categories"]])
        acts = st.text_area("개선조치 후보 (검토용, 한 줄에 하나)", "\n".join(base.get("suggested_actions", [])), height=110)
        miss = st.text_area("추가 확인 필요 사항 (한 줄에 하나)", "\n".join(base.get("missing_information", [])), height=80)
        insuff = st.checkbox("정보 부족 — 확인 필요", value=bool(base.get("insufficient_information")))
        review = st.checkbox("안전관리자 검토 필요", value=bool(base.get("review_required", True)))
        note = st.text_input("수정 메모 (선택)")
        submitted = st.form_submit_button("작업자 확인 및 저장", type="primary")
    if submitted:
        old = {h["code"]: h for h in base.get("observed_hazards", [])}
        out = {
            "cleaned_description": desc,
            "hazard_category": cats,
            "observed_hazards": [old.get(c) or {"code": c, "name": CODES[c]["name"], "description": "(작업자 추가)", "evidence": ""}
                                 for c in codes],
            "suggested_actions": [x.strip() for x in acts.splitlines() if x.strip()],
            "missing_information": [x.strip() for x in miss.splitlines() if x.strip()],
            "insufficient_information": insuff,
            "review_required": review,
        }
        rev = storage.add_revision(rec, out, note)
        st.session_state["last_rev"] = rev
        st.rerun()
    if st.session_state.get("last_rev"):
        rev = st.session_state["last_rev"]
        st.success(f"수정본 {rev['revision']} 저장 · 분석 결과에서 수정: {rev['edited_from_ai']} · 바뀐 항목: {rev['changed_fields']}")
        st.button("결과 확인으로 이동", on_click=go, args=("C. 결과 확인",))


# ---------------- C. 결과 ----------------
def page_results() -> None:
    st.header("C. 결과 확인")
    recs = storage.list_records()
    if recs:
        rows = []
        for r in recs:
            revs = r.get("user_revisions") or []
            ai = r.get("ai_result") or {}
            t = r["timeline"]
            dur = None
            if t.get("reviewed"):
                dur = (datetime.fromisoformat(t["reviewed"]) - datetime.fromisoformat(t["created"])).total_seconds()
            rows.append({
                "사건 ID": r["record_id"], "사건명": r["title"], "장소": r["location"],
                "분석": (ai.get("info") or {}).get("backend", "-"),
                "AI 추론": ai.get("is_ai_inference", False),
                "분석 시도": len(r.get("analysis_attempts") or []) or (1 if ai else 0),
                "확인": bool(revs), "AI 결과 수정": revs[-1]["edited_from_ai"] if revs else None,
                "신고→확인(초)": dur, "사진": len(r.get("photos", [])), "음성": bool(r.get("audio")),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)
    rec = pick_record("보고서를 볼 신고")
    if not rec:
        return
    html = report.render(rec)
    c1, c2 = st.columns(2)
    if c1.button("HTML 보고서 파일로 저장"):
        p = report.write_report(rec)
        st.success(f"저장: {p}")
    c2.download_button("보고서 내려받기 (.html, 브라우저에서 인쇄 → PDF 저장)", html.encode("utf-8"),
                       file_name=f"{rec['record_id']}_report.html", mime="text/html")
    components.html(html, height=900, scrolling=True)


{"A. 위험요인 신고": page_report, "B. AI 보고서 검토": page_review, "C. 결과 확인": page_results}[page]()

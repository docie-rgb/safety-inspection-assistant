"""최초 위험요인 신고서(HTML) 생성. 브라우저의 인쇄 → PDF 저장으로 PDF 를 만들 수 있습니다."""
from __future__ import annotations

import base64
import mimetypes
from html import escape
from pathlib import Path

from .config import load_settings
from .storage import now, record_dir


def _li(items) -> str:
    items = [i for i in (items or []) if str(i).strip()]
    return "<ul>" + "".join(f"<li>{escape(str(i))}</li>" for i in items) + "</ul>" if items else "<p class='muted'>없음</p>"


def _img(rec: dict, rel: str) -> str:
    p = record_dir(rec["record_id"]) / rel
    if not p.exists():
        return f"<p class='muted'>사진 파일 없음: {escape(rel)}</p>"
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f"<figure><img src='data:{mime};base64,{b64}' alt='첨부 사진 {escape(rel)}'><figcaption>{escape(rel)} (AI 미분석)</figcaption></figure>"


def render(rec: dict) -> str:
    cfg = load_settings()
    ai = rec.get("ai_result") or {}
    revs = rec.get("user_revisions") or []
    final = revs[-1]["output"] if revs else ai.get("output") or {}
    status = (f"작업자 확인 완료 (수정본 {revs[-1]['revision']}, "
              f"{'AI 결과에서 수정함' if revs[-1]['edited_from_ai'] else 'AI 결과 그대로 확인'})"
              if revs else "작업자 확인 전 (AI 초안)")
    info = ai.get("info") or {}
    if ai.get("is_ai_inference"):
        kind = "AI 추론 결과"
    elif ai.get("error"):
        kind = f"AI 분석 실패 — 작업자 작성 ({ai.get('error')})"
    elif info.get("backend") == "rule":
        kind = "규칙 기반 결과(AI 아님)"
    elif info.get("backend") == "manual":
        kind = "수동 작성(분석 없음)"
    else:
        kind = "AI 추론 없음"
    n_attempts = len(rec.get("analysis_attempts") or [])
    if n_attempts > 1:
        kind += f" · 분석 시도 {n_attempts}회 중 마지막 결과"
    stt = (rec.get("input") or {}).get("stt") or {}
    hazards = "".join(
        f"<tr><td>{escape(h.get('name', ''))}<br><code>{escape(h.get('code', ''))}</code></td>"
        f"<td>{escape(h.get('description', ''))}</td><td>“{escape(h.get('evidence', ''))}”</td></tr>"
        for h in final.get("observed_hazards", [])
    ) or "<tr><td colspan=3 class='muted'>없음</td></tr>"
    photos = "".join(_img(rec, p["path"]) for p in rec.get("photos", [])) or "<p class='muted'>첨부 사진 없음</p>"
    insuff = final.get("insufficient_information")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>위험요인 신고서 {escape(rec['record_id'])}</title>
<style>
body{{font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#111;line-height:1.55}}
h1{{font-size:22px;border-bottom:2px solid #111;padding-bottom:6px}} h2{{font-size:16px;margin-top:22px;background:#f1f1f1;padding:4px 8px}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #bbb;padding:6px;vertical-align:top;font-size:14px}} th{{background:#fafafa;text-align:left;width:22%}}
.muted{{color:#777}} .warn{{background:#fff4d6;border:1px solid #e0b84c;padding:8px}} .tag{{display:inline-block;border:1px solid #888;border-radius:4px;padding:0 6px;margin-right:4px}}
figure{{display:inline-block;margin:6px}} img{{max-width:260px;max-height:200px;border:1px solid #ccc}} figcaption{{font-size:12px;color:#555}}
@media print{{h2{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}}}
</style></head><body>
<h1>최초 위험요인 신고서 (초안)</h1>
<table>
<tr><th>사건 ID</th><td>{escape(rec['record_id'])}</td></tr>
<tr><th>사건명</th><td>{escape(rec.get('title') or '')}</td></tr>
<tr><th>장소</th><td>{escape(rec.get('location') or '(입력 없음)')}</td></tr>
<tr><th>신고 일시</th><td>{escape(rec['timeline'].get('created', ''))}</td></tr>
<tr><th>확인 상태</th><td>{escape(status)}</td></tr>
<tr><th>정보 부족</th><td>{'<b>확인 필요</b>' if insuff else '아니오'}</td></tr>
</table>
<h2>신고 원문</h2><p>{escape(rec['input'].get('text') or '(텍스트 입력 없음)')}</p>
<h2>음성 전사(STT)</h2>
<p>{('전사 원문: ' + escape(stt.get('text', ''))) if stt else '<span class="muted">음성 입력 없음</span>'}</p>
{('<p>사용자 수정 전사문: ' + escape(rec['input'].get('stt_edited_text') or '') + '</p>') if stt else ''}
<h2>정리된 현장 설명</h2><p>{escape(final.get('cleaned_description', ''))}</p>
<h2>위험유형</h2><p>{''.join(f"<span class='tag'>{escape(c)}</span>" for c in final.get('hazard_category', [])) or '<span class="muted">없음</span>'}</p>
<h2>관찰된 위험요인 (원문 근거)</h2>
<table><tr><th>위험요인</th><th>설명</th><th>원문 근거</th></tr>{hazards}</table>
<h2>개선조치 후보 (검토용 — 확정 지시 아님)</h2>{_li(final.get('suggested_actions'))}
<h2>추가 확인 필요 사항</h2>{_li(final.get('missing_information'))}
<h2>첨부 사진 (참고자료 — AI가 분석하지 않음)</h2>{photos}
<h2>생성 정보</h2>
<table>
<tr><th>결과 종류</th><td>{escape(kind)}</td></tr>
<tr><th>모델</th><td>{escape(str(info.get('model', '-')))}</td></tr>
<tr><th>실행 위치</th><td>{escape(str(info.get('execution_location', '-')))}</td></tr>
<tr><th>데이터 전송</th><td>{escape(str(info.get('data_transfer', '-')))}</td></tr>
<tr><th>분석 시각 / 소요</th><td>{escape(str(ai.get('started_at_utc', '-')))} / {ai.get('elapsed_sec', '-')} 초</td></tr>
<tr><th>보고서 생성</th><td>{now()}</td></tr>
</table>
<p class="warn">{escape(cfg['report']['disclaimer'])}</p>
</body></html>"""


def write_report(rec: dict) -> Path:
    d = record_dir(rec["record_id"]) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    n = len(list(d.glob("report_*.html"))) + 1
    p = d / f"report_{n:03d}.html"  # 이전 보고서를 덮어쓰지 않음
    p.write_text(render(rec), encoding="utf-8")
    return p

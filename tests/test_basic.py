"""AI 없이 실행 가능한 기본 동작 테스트.  실행: python -m unittest -v"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from safety_assistant import report, rule_engine, storage  # noqa: E402
from safety_assistant.checks import check_output  # noqa: E402
from safety_assistant.config import path_of  # noqa: E402
from safety_assistant.engine import analyze  # noqa: E402


class TestDataset(unittest.TestCase):
    def test_gold_locked(self):
        lock = json.loads(path_of("gold_lock").read_text(encoding="utf-8"))
        for rel, h in lock["files"].items():
            self.assertEqual(hashlib.sha256((ROOT / rel).read_bytes()).hexdigest(), h, rel)

    def test_counts(self):
        gold = [json.loads(l) for l in path_of("gold").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(gold), 100)
        self.assertEqual(sum(g["difficulty"] == "hard" for g in gold), 20)
        cases = [json.loads(l) for l in path_of("cases").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all("hazard_codes" not in c for c in cases), "모델 입력에 정답이 섞이면 안 됨")


class TestRule(unittest.TestCase):
    def codes(self, text, loc="A동"):
        return sorted(h["code"] for h in rule_engine.classify(text, loc)["observed_hazards"])

    def test_simple(self):
        self.assertEqual(self.codes("배전반 앞에 박스가 쌓여 있습니다."), ["ELEC_PANEL_BLOCKED"])

    def test_normal(self):
        self.assertEqual(self.codes("점검 결과 특이사항 없습니다."), ["NONE"])

    def test_unknown_and_location(self):
        r = rule_engine.classify("뭔가 이상해요", "")
        self.assertTrue(r["insufficient_information"])
        self.assertIn("장소", r["missing_information"])


class TestEngine(unittest.TestCase):
    def test_empty_text_no_inference(self):
        r = analyze("", "A동", "ollama")
        self.assertFalse(r["is_ai_inference"])
        self.assertEqual([h["code"] for h in r["output"]["observed_hazards"]], ["UNKNOWN_NEEDS_CHECK"])

    def test_fabrication_check(self):
        out = {"cleaned_description": "작업자가 부상을 입었습니다.", "observed_hazards": [
            {"code": "SLIP_FLOOR_WET", "description": "", "evidence": "바닥이 젖음"}]}
        c = check_output("바닥에 물이 있어요", "식당", out)
        self.assertIn("부상", c["fabrication_terms"])
        self.assertEqual(len(c["evidence_not_in_source"]), 1)


class TestStorageReport(unittest.TestCase):
    def test_roundtrip(self):
        rec = storage.create("테스트", "A동", "통로에 자재가 있어요")
        try:
            rec["ai_result"] = analyze(rec["input"]["text"], "A동", "rule")
            storage.save(rec)
            png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a4a20000000049454e44ae426082")
            storage.attach_file(rec, "p.png", png, "photo")
            storage.attach_file(rec, "p.png", png, "photo")
            self.assertEqual(len({p["path"] for p in rec["photos"]}), 2, "같은 이름 사진도 덮어쓰지 않음")
            out = dict(rec["ai_result"]["output"])
            r1 = storage.add_revision(rec, out)
            self.assertFalse(r1["edited_from_ai"])
            out2 = dict(out, cleaned_description="수정함")
            r2 = storage.add_revision(rec, out2)
            self.assertTrue(r2["edited_from_ai"])
            self.assertEqual(len(storage.load(rec["record_id"])["user_revisions"]), 2)
            html = report.render(storage.load(rec["record_id"]))
            self.assertIn("규칙 기반 결과(AI 아님)", html)
            self.assertIn("AI 미분석", html)
            p1, p2 = report.write_report(rec), report.write_report(rec)
            self.assertNotEqual(p1, p2)
        finally:
            storage.delete_record_dir_for_tests(rec["record_id"])

    def test_reject_bad_ext(self):
        rec = storage.create("t", "", "")
        try:
            with self.assertRaises(ValueError):
                storage.attach_file(rec, "x.exe", b"x", "photo")
        finally:
            storage.delete_record_dir_for_tests(rec["record_id"])


class TestFallbackAndRetry(unittest.TestCase):
    def test_attempts_append_and_fingerprint(self):
        from safety_assistant.engine import manual_result

        fp = storage.fingerprint("t", "A동", "통로에 자재가 있어요", "", [b"img"])
        self.assertEqual(fp, storage.fingerprint("t ", "A동", "통로에 자재가 있어요", "", [b"img"]))
        self.assertNotEqual(fp, storage.fingerprint("t", "A동", "통로에 자재가 있어요", "", [b"img2"]))
        rec = storage.create("t", "A동", "통로에 자재가 있어요", fp)
        try:
            self.assertEqual(storage.find_recent_by_fingerprint(fp)["record_id"], rec["record_id"])
            failed = {"backend": "ollama", "is_ai_inference": False, "error": "BackendStalled: x", "output": {}}
            storage.record_analysis(rec, failed)
            storage.record_analysis(rec, analyze("통로에 자재가 있어요", "A동", "rule"))
            storage.record_analysis(rec, manual_result("통로에 자재가 있어요", "A동"))
            r = storage.load(rec["record_id"])
            self.assertEqual([a["attempt"] for a in r["analysis_attempts"]], [1, 2, 3])
            self.assertEqual(r["analysis_attempts"][0]["error"], "BackendStalled: x", "실패 기록 보존")
            self.assertEqual(r["ai_result"]["backend"], "manual")
            self.assertFalse(r["ai_result"]["is_ai_inference"])
            self.assertEqual(len(storage.list_records()), len({x["record_id"] for x in storage.list_records()}))
            rev = storage.add_revision(r, dict(r["ai_result"]["output"], hazard_category=["전기"]))
            self.assertEqual(rev["based_on_attempt"], 3)
            self.assertIn("수동 작성", report.render(storage.load(rec["record_id"])))
        finally:
            storage.delete_record_dir_for_tests(rec["record_id"])

    def test_anthropic_disabled_without_key(self):
        import os

        from safety_assistant import llm_engine

        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            ok, msg = llm_engine.is_available("anthropic")
            self.assertFalse(ok)
            r = analyze("배전반 앞에 박스", "A동", "anthropic")
            self.assertFalse(r["is_ai_inference"])
            self.assertIn("ANTHROPIC_API_KEY", r["error"])
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved

    def test_ollama_stall_detected(self):
        """응답을 시작하지 않는 가짜 서버 → 제한 시간을 기다리지 않고 BackendStalled."""
        import http.server
        import threading
        import time
        from unittest import mock

        from safety_assistant import llm_engine
        from safety_assistant.config import load_settings

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # /api/ps
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"models":[]}')

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                if H.mode == "no_headers":  # 실제 Ollama 처럼 헤더조차 보내지 않음(모델 로딩/프롬프트 단계 멈춤)
                    time.sleep(5)
                    return
                self.send_response(200); self.send_header("Content-Type", "application/x-ndjson"); self.end_headers()
                self.wfile.write(b'{"message":{"content":"{"},"done":false}\n'); self.wfile.flush()
                time.sleep(5)  # 첫 조각 뒤 생성 도중 멈춤

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        import copy

        s = copy.deepcopy(load_settings())  # 캐시된 설정을 바꾸지 않도록 복사
        s["llm"]["backends"]["ollama"].update(base_url=f"http://127.0.0.1:{srv.server_port}",
                                              first_token_timeout_sec=1, stall_timeout_sec=1, max_total_sec=3)
        try:
            with mock.patch.object(llm_engine, "load_settings", return_value=s):
                for mode, phase in [("no_headers", "load_or_prompt"), ("mid_stream", "generation")]:
                    H.mode = mode
                    t0 = time.perf_counter()
                    with self.assertRaises(llm_engine.BackendStalled) as cm:
                        llm_engine.call_ollama("sys", "user")
                    self.assertLess(time.perf_counter() - t0, 2.5, mode)
                    self.assertEqual(cm.exception.diagnostics["phase"], phase)
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()

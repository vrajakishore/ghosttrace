"""Rules that must hold whatever the numbers are."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from services.agent.evidence import grounding_check, write_foundry_pack, write_pack
from services.common.config import ROOT, load_config
from services.common.db import connect, put
from services.correlate.engine import build_pools, write_cases
from services.sim.simulate import simulate
from services.vision import ai_match
from services.vision.raster import render_frame
from services.vision.simulated import SimulatedVision, vehicle_marks


def small_config() -> dict:
    cfg = load_config()
    cfg["traffic"]["vehicles_per_day"], cfg["traffic"]["days"] = 2500, 2
    return cfg


class PipelineNeverReadsTruth(unittest.TestCase):
    def test_engine_source_has_no_truth_access(self):
        src = (ROOT / "services" / "correlate" / "engine.py").read_text(encoding="utf-8")
        code = "\n".join(line.split("#")[0] for line in src.split('"""')[::2] for line in line.splitlines())
        for banned in ("truth_vehicle_id", "ghost_kind", "FROM crossings", "JOIN crossings", "vehicles", "trips"):
            self.assertNotIn(banned, code, f"the matching engine must not use `{banned}`")


class Simulator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = small_config()
        cls.con = connect(Path(":memory:"), create=True)
        cls.stats = simulate(cls.cfg, cls.con)

    def one(self, sql):
        return self.con.execute(sql).fetchone()[0]

    def test_same_seed_gives_same_output(self):
        other = connect(Path(":memory:"), create=True)
        simulate(small_config(), other)
        q = "SELECT COUNT(*), ROUND(SUM(ts), 3), GROUP_CONCAT(DISTINCT system_class) FROM crossings"
        self.assertEqual(tuple(self.con.execute(q).fetchone()), tuple(other.execute(q).fetchone()))

    def test_every_registration_is_fictitious(self):
        self.assertEqual(self.one("SELECT COUNT(*) FROM vahan WHERE vrn NOT LIKE 'XX%'"), 0)
        self.assertEqual(self.one("SELECT COUNT(*) - COUNT(DISTINCT vrn) FROM vahan"), 0)

    def test_roster_includes_indian_market_models(self):
        models = {tuple(row) for row in self.con.execute("SELECT make, model FROM vahan")}
        self.assertIn(("Hyundai", "Creta"), models)
        self.assertIn(("Mahindra", "Thar"), models)

    def test_ghost_share_is_near_the_configured_value(self):
        share = self.one("SELECT AVG(system_class = 'unidentified') FROM crossings WHERE plaza_type = 'mlff'")
        self.assertAlmostEqual(share, self.cfg["evasion"]["ghost_share"], delta=0.008)

    def test_legacy_plazas_never_record_a_ghost(self):
        self.assertEqual(self.one("SELECT COUNT(*) FROM crossings WHERE plaza_type = 'legacy' "
                                  "AND system_class = 'unidentified'"), 0)

    def test_busiest_gantry_matches_the_configured_volume(self):
        busiest = self.one("SELECT MAX(n) FROM (SELECT COUNT(*) n FROM crossings WHERE plaza_type = 'mlff' GROUP BY plaza_id)")
        per_day = busiest / self.cfg["traffic"]["days"]
        self.assertAlmostEqual(per_day / self.cfg["traffic"]["vehicles_per_day"], 1.0, delta=0.05)

    def test_time_moves_forward_along_every_trip(self):
        bad = self.one("SELECT COUNT(*) FROM crossings a JOIN crossings b ON a.trip_id = b.trip_id "
                       "AND b.event_id = a.event_id + 1 WHERE b.ts <= a.ts")
        self.assertEqual(bad, 0)


CASE = {"decision": "recommend", "top_vrn": "XX12AB000123",
    "settings": {"window": 20, "threshold": 0.7, "margin": 0.1},
        "ghost": {"plaza_id": "M1", "time": "Day 2, 08:15", "direction": "towards L2", "plate_status": "obstructed_deliberate",
                  "observed": {"make": "Kaveri", "model": "Dart", "colour": "white", "body": "hatchback"}},
        "candidates": [{"vrn": "XX12AB000123", "plaza_id": "L2", "time": "Day 2, 08:46", "travel_minutes": 31.0,
                        "offset_minutes": 1.0, "image_similarity": 0.84, "in_window": True, "passes": True},
                       {"vrn": "XX34CD000456", "plaza_id": "L2", "time": "Day 2, 08:44", "travel_minutes": 29.0,
                        "offset_minutes": -1.0, "image_similarity": 0.41, "in_window": True, "passes": False}]}


class Grounding(unittest.TestCase):
    def test_template_pack_is_grounded(self):
        pack = write_pack(CASE)
        self.assertTrue(pack["grounding_ok"], pack["ungrounded_items"])
        self.assertIn("XX12AB000123", pack["text"])
        self.assertIn("not a penalty", pack["text"])
        self.assertIn("No real images were processed", pack["text"])

    def test_invented_registration_plaza_and_time_are_caught(self):
        text = "Vehicle XX99ZZ999999 was seen at M3 at Day 9, 23:59 and also XX12AB000123 at L2."
        self.assertEqual(grounding_check(text, CASE), ["Day 9, 23:59", "M3", "XX99ZZ999999"])

    def test_foundry_pack_preserves_decision_and_uses_validated_explanation(self):
        answer = {"decision": "recommend", "named_vrn": "XX12AB000123",
                  "summary": "The candidate at L2 is the engine's recommendation.",
                  "reasons": ["Its timing and simulated image similarity pass the configured checks."],
                  "reviewer_checks": ["Check the L2 crossing at Day 2, 08:46."],
                  "honest_failure_possible": False,
                  "notice": "Simulated data. Recommendation for human review only; not a penalty."}
        with patch("services.agent.evidence.is_foundry_agent_enabled", return_value=True), patch(
            "services.agent.evidence.complete_prompt_agent_json", return_value=answer,
        ):
            pack = write_foundry_pack(CASE)
        self.assertEqual(pack["source"], "ghosttrace-explainer-prompt")
        self.assertTrue(pack["grounding_ok"])
        self.assertIn("human review only", pack["text"])

    def test_foundry_pack_falls_back_for_ungrounded_output(self):
        answer = {"decision": "recommend", "named_vrn": "XX12AB000123",
                  "summary": "The candidate was seen at M9.", "reasons": ["It is plausible."],
                  "reviewer_checks": ["Review the image."], "honest_failure_possible": False,
                  "notice": "Simulated data. Recommendation for human review only; not a penalty."}
        with patch("services.agent.evidence.is_foundry_agent_enabled", return_value=True), patch(
            "services.agent.evidence.complete_prompt_agent_json", return_value=answer,
        ):
            pack = write_foundry_pack(CASE)
        self.assertEqual(pack["source"], "template (fallback)")
        self.assertTrue(pack["grounding_ok"])

    def test_foundry_pack_falls_back_when_it_requests_unavailable_images(self):
        answer = {"decision": "recommend", "named_vrn": "XX12AB000123",
                  "summary": "The candidate at L2 has simulated image similarity 0.84.",
                  "reasons": ["Its timing fits the configured window."],
                  "reviewer_checks": ["Inspect the image evidence at L2."], "honest_failure_possible": False,
                  "notice": "Simulated data. Recommendation for human review only; not a penalty."}
        with patch("services.agent.evidence.is_foundry_agent_enabled", return_value=True), patch(
            "services.agent.evidence.complete_prompt_agent_json", return_value=answer,
        ):
            pack = write_foundry_pack(CASE)
        self.assertEqual(pack["source"], "template (fallback)")
        self.assertIn("No real images were processed", pack["text"])


class AiVision(unittest.TestCase):
    def test_frame_is_a_valid_png_with_fixed_size(self):
        import random
        import struct
        png = render_frame({"model": "Creta", "colour": "white", "body": "suv",
                            "marks": [{"kind": "stripe", "colour": "red"}]}, "glare_weather", random.Random(1), label="CAND 3")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(struct.unpack(">II", png[16:24]), (320, 240))

    def test_marks_are_stable_per_vehicle(self):
        self.assertEqual(vehicle_marks(7, 42, "suv", "white"), vehicle_marks(7, 42, "suv", "white"))

    def test_rule_names_only_a_clear_winner(self):
        ghost = [{"kind": "stripe", "colour": "blue", "side": None}]
        same = ai_match.compare(ghost, [{"kind": "stripe", "colour": "Blue", "side": None}], False)
        other = ai_match.compare(ghost, [{"kind": "stripe", "colour": "red", "side": None}], False)
        bare = ai_match.compare(ghost, [], False)
        self.assertGreaterEqual(same["score"], ai_match.RECOMMEND_SCORE)
        self.assertTrue(other["conflicting"] and bare["conflicting"])
        self.assertEqual(ai_match.decide({1: same["score"], 2: other["score"]}), ("recommend", 1))
        self.assertEqual(ai_match.decide({1: same["score"], 2: same["score"]}), ("review", None))

    def test_match_maps_labels_to_registrations(self):
        lineup = [{"vrn": "XX11AA000001", "plaza_id": "L1", "time": "Day 2, 08:00", "offset_minutes": 1.0,
                   "registered": {}}, {"vrn": "XX11AA000002", "plaza_id": "L1", "time": "Day 2, 08:02",
                                      "offset_minutes": 3.0, "registered": {}}]
        frame = lambda label, marks: {"label": label, "body": "suv", "colour": "white", "plate": "covered", "marks": marks}
        rack = [{"kind": "roof_rack", "colour": None, "side": None}]
        answer = {"frames": [frame("CAND 2", rack), frame("GHOST", rack), frame("CAND 1", [])], "notes": "Clear frames."}
        with patch.object(ai_match, "is_enabled", return_value=True), patch.object(ai_match, "complete_json", return_value=answer):
            r = ai_match.match(b"g", lineup, [b"1", b"2"])
        self.assertEqual((r["status"], r["decision"], r["named_vrn"]), ("ok", "recommend", "XX11AA000002"))
        answer["frames"].pop()
        with patch.object(ai_match, "is_enabled", return_value=True), patch.object(ai_match, "complete_json", return_value=answer):
            self.assertEqual(ai_match.match(b"g", lineup, [b"1", b"2"])["status"], "unavailable")

    def test_vision_code_never_reads_truth(self):
        src = (ROOT / "services" / "vision" / "ai_match.py").read_text(encoding="utf-8")
        for banned in ("truth_vehicle_id", "ghost_kind", "SELECT", "sqlite", "simulated import", "trip_id"):
            self.assertNotIn(banned, src)


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["GT_DB"] = str(Path(cls.tmp.name) / "t.db")
        cfg = small_config()
        con = connect(Path(os.environ["GT_DB"]), create=True)
        simulate(cfg, con)
        build_pools(con, cfg, SimulatedVision(con, cfg))
        settings = {"preset": "central", "window": 60, "threshold": 0.7, "margin": 0.0}
        put(con, "tuned_settings", json.dumps(settings))
        put(con, "tuned_all", json.dumps({"central": settings}))
        cls.counts = write_cases(con, settings)
        con.close()
        from http.server import ThreadingHTTPServer
        from services.api import server
        orig = server.load_config
        server.load_config = lambda: cfg
        try:
            cls.app = server.App()
            cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.app))
        finally:
            server.load_config = orig
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.app.con.close()
        os.environ.pop("GT_DB", None)
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return json.loads(r.read())

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), method="POST")
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_metrics_add_up(self):
        for q in ("method=reported&window=10", "method=full&window=60&threshold=0.7&preset=central"):
            m = self.get("/api/metrics?" + q)
            self.assertEqual(m["recommend"] + m["review"] + m["unresolved"], m["ghosts"])
            self.assertEqual(m["correct"] + m["wrong"], m["recommend"])

    def test_case_detail_never_exposes_truth(self):
        listing = self.get("/api/cases?decision=recommend&limit=5")
        self.assertEqual(listing["total"], self.counts["recommend"])
        detail = self.get(f"/api/cases/{listing['cases'][0]['case_id']}")
        self.assertTrue(detail["evidence_pack"]["grounding_ok"])
        self.assertNotIn("truth", json.dumps(detail).lower())
        self.assertNotIn("ghost_kind", json.dumps(detail))

    def test_review_is_written_to_the_audit_log(self):
        case_id = self.get("/api/cases?decision=recommend&limit=1")["cases"][0]["case_id"]
        self.assertTrue(self.post(f"/api/cases/{case_id}/review", {"decision": "reject", "note": "unit test"})["ok"])
        self.assertEqual(self.get("/api/audit")[0]["case_id"], case_id)
        self.assertEqual(self.get(f"/api/cases/{case_id}")["review"]["decision"], "reject")
        with self.assertRaises(urllib.error.HTTPError) as bad:
            self.post(f"/api/cases/{case_id}/review", {"decision": "fine_him"})
        self.assertEqual(bad.exception.code, 400)

    def test_foundry_status_and_evidence_route_fall_back_when_disabled(self):
        with patch.dict(os.environ, {"FOUNDRY_AGENT_ENABLED": "", "FOUNDRY_PROJECT_ENDPOINT": ""}):
            self.assertFalse(self.get("/api/health")["foundry_agent_enabled"])
            item = self.get("/api/cases?decision=recommend&limit=1")["cases"][0]
            case = self.get(f"/api/cases/{item['case_id']}")
            case["truth"] = {"secret": "ignored"}
            pack = self.post("/v1/evidence-pack", case)
        self.assertEqual(pack["source"], "template")
        self.assertTrue(pack["grounding_ok"])

    def test_frames_and_vision_route_fall_back_when_disabled(self):
        case_id = self.get("/api/cases?decision=recommend&limit=1")["cases"][0]["case_id"]
        with urllib.request.urlopen(f"{self.base}/api/cases/{case_id}/frames/0.png") as r:
            self.assertEqual(r.headers["Content-Type"], "image/png")
            self.assertTrue(r.read().startswith(b"\x89PNG"))
        with self.assertRaises(urllib.error.HTTPError) as bad:
            urllib.request.urlopen(f"{self.base}/api/cases/{case_id}/frames/99.png")
        self.assertEqual(bad.exception.code, 404)
        with patch.dict(os.environ, {"MODEL_BASE_URL": "", "MODEL_NAME": ""}):
            self.assertFalse(self.get("/api/health")["vision_ai_enabled"])
            r = self.post("/v1/vision-match", {"case_id": case_id})
        self.assertEqual(r["status"], "unavailable")
        self.assertTrue(r["engine_decision_unchanged"])
        self.assertNotIn("anchor_event_id", json.dumps(r))

    def test_replay_and_static_files(self):
        r = self.get("/api/replay?day=1&start_hour=8&minutes=60")
        self.assertGreater(len(r["trips"]), 100)
        with urllib.request.urlopen(self.base + "/") as page:
            self.assertIn(b"SIMULATED DATA", page.read())
        with self.assertRaises(urllib.error.HTTPError) as bad:
            urllib.request.urlopen(self.base + "/../config/corridor.json")
        self.assertEqual(bad.exception.code, 404)


if __name__ == "__main__":
    unittest.main()

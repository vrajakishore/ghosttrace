"""HTTP API and static web app. Standard library only.

GET  /api/summary
GET  /api/metrics?method=reported|full&window=&threshold=&preset=
GET  /api/curve?method=&window=&preset=
GET  /api/cases?decision=&limit=&offset=
GET  /api/cases/{id}            GET /api/cases/{id}/truth  (simulation only)
GET  /api/cases/{id}/frames/{n}.png   synthetic camera frame: 0 = ghost, n = AI line-up candidate n
POST /api/cases/{id}/review     {"decision": "confirm|reject|need_more", "note": "", "reviewer": ""}
POST /v1/vision-match           {"case_id": n}  AI compares the frames; the engine decision is unchanged
GET  /api/replay?day=&start_hour=&minutes=
GET  /api/audit
"""
from __future__ import annotations

import json
import mimetypes
import threading
from datetime import datetime, timezone
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from services.agent.evidence import write_foundry_pack, write_pack
from services.common.config import ROOT, db_path, load_config
from services.common.db import connect, get
from services.common.fleet import speed_class_of
from services.common.llm import is_foundry_agent_enabled
from services.correlate.engine import PRESETS
from services.eval.metrics import THRESHOLDS, WINDOWS, evaluate, load_cases
from services.vision import ai_match
from services.vision.simulated import SimulatedCamera

WEB = ROOT / "services" / "web"


def clock(ts: float) -> str:
    day, rem = divmod(int(ts), 86400)
    return f"Day {day + 1}, {rem // 3600:02d}:{rem % 3600 // 60:02d}"


class App:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.con = connect(db_path())
        self.lock = threading.Lock()
        self.results = json.loads(get(self.con, "results") or "{}")
        self.settings = json.loads(get(self.con, "tuned_settings") or "null")
        self.tuned = json.loads(get(self.con, "tuned_all") or "{}")
        self.test = [c for c in load_cases(self.con, self.cfg) if c.day > 0]
        self.km = {p["id"]: p["km"] for p in self.cfg["plazas"]}
        self.camera = SimulatedCamera(self.con, self.cfg["seed"])
        self.vision_results: dict[int, dict] = {}

    def q(self, sql: str, args: tuple = ()) -> list:
        with self.lock:
            return self.con.execute(sql, args).fetchall()

    # ---- metrics -------------------------------------------------------
    def _clean(self, r: dict) -> dict:
        keep = ("ghosts", "reachable", "recommend", "correct", "wrong", "review", "unresolved",
                "precision", "precision_ci", "resolution", "window", "method", "threshold", "margin", "preset", "scope")
        return {k: r[k] for k in keep}

    @lru_cache(maxsize=512)
    def metrics(self, method: str, window: float, threshold: float, preset: str) -> dict:
        if method == "reported":
            return self._clean(evaluate(self.test, window, "reported", scope="nearest"))
        margin = (self.tuned.get(preset) or {}).get("margin", 0.0)
        return self._clean(evaluate(self.test, window, "full", threshold, margin, preset))

    def curve(self, method: str, window: float, preset: str) -> list[dict]:
        if method == "reported":
            return [self.metrics("reported", w, 0.0, preset) for w in WINDOWS]
        return [self.metrics("full", window, t, preset) for t in THRESHOLDS]

    # ---- cases ---------------------------------------------------------
    def case_list(self, decision: str | None, limit: int, offset: int) -> dict:
        where, args = ("WHERE k.decision = ?", (decision,)) if decision else ("", ())
        rows = self.q(
            "SELECT k.case_id, k.decision, k.top_vrn, k.top_score, k.n_candidates, k.reviewer_decision, "
            "c.plaza_id, c.ts, c.obs_make, c.obs_model, c.obs_colour, c.obs_body, c.plate_status "
            f"FROM cases k JOIN pipeline_crossings c ON c.event_id = k.ghost_event_id {where} "
            "ORDER BY c.ts DESC LIMIT ? OFFSET ?", args + (limit, offset))
        total = self.q(f"SELECT COUNT(*) FROM cases k {where}", args)[0][0]
        return {"total": total, "cases": [
            {"case_id": r[0], "decision": r[1], "top_vrn": r[2], "top_score": r[3], "n_candidates": r[4],
             "reviewer_decision": r[5], "plaza_id": r[6], "time": clock(r[7]),
             "observed": f"{r[10]} {r[8]} {r[9]}", "body": r[11], "colour": r[10], "plate_status": r[12]}
            for r in rows]}

    def case_detail(self, case_id: int) -> dict | None:
        rows = self.q(
            "SELECT k.decision, k.top_vrn, k.n_candidates, k.reviewer_decision, k.reviewer_id, k.reviewed_at, "
            "c.plaza_id, c.ts, c.direction, c.plate_status, c.obs_make, c.obs_model, c.obs_colour, c.obs_body "
            "FROM cases k JOIN pipeline_crossings c ON c.event_id = k.ghost_event_id WHERE k.case_id = ?", (case_id,))
        if not rows:
            return None
        r, s = rows[0], self.settings
        col = f"sim_{s['preset']}"
        best: dict[str, dict] = {}
        for c in self.q(
                f"SELECT k.vrn, k.plaza_id, k.dt_minutes, k.offset_minutes, k.{col}, a.ts, v.make, v.model, v.colour, v.body "
                "FROM candidates k JOIN pipeline_crossings a ON a.event_id = k.anchor_event_id "
                "JOIN vahan v ON v.vrn = k.vrn WHERE k.case_id = ?", (case_id,)):
            in_window = abs(c[3]) <= s["window"] / 2
            item = {"vrn": c[0], "plaza_id": c[1], "travel_minutes": round(c[2], 1), "offset_minutes": round(c[3], 1),
                    "image_similarity": round(c[4], 3), "time": clock(c[5]), "in_window": in_window,
                    "passes": in_window and c[4] >= s["threshold"],
                    "registered": {"make": c[6], "model": c[7], "colour": c[8], "body": c[9]}}
            if c[0] not in best or item["image_similarity"] > best[c[0]]["image_similarity"]:
                best[c[0]] = item
        legacy_ahead = [p["id"] for p in self.cfg["plazas"] if p["type"] == "legacy"
                        and (p["km"] - self.km[r[6]]) * r[8] > 0]
        case = {"case_id": case_id, "decision": r[0], "top_vrn": r[1], "n_candidates": r[2], "settings": s,
                "ghost": {"plaza_id": r[6], "time": clock(r[7]), "plate_status": r[9],
                          "direction": f"towards {legacy_ahead[0]}" if legacy_ahead else "outbound",
                          "speed_class": speed_class_of(r[13]),
                          "observed": {"make": r[10], "model": r[11], "colour": r[12], "body": r[13]}},
                "candidates": sorted(best.values(), key=lambda c: -c["image_similarity"])[:12],
                "review": {"decision": r[3], "reviewer": r[4], "at": r[5]} if r[3] else None}
        case["evidence_pack"] = write_pack(case)
        return case

    def evidence_pack(self, case_id: int) -> dict | None:
        case = self.case_detail(case_id)
        return write_foundry_pack(case) if case else None

    # ---- AI vision -----------------------------------------------------
    def vision_lineup(self, case_id: int) -> list[dict]:
        """Time-feasible candidates, one anchor crossing per registration: the closest in time, in registration order."""
        half = self.settings["window"] / 2
        best: dict[str, dict] = {}
        for c in self.q(
                "SELECT k.vrn, k.anchor_event_id, k.plaza_id, k.offset_minutes, a.ts, v.make, v.model, v.colour, v.body "
                "FROM candidates k JOIN pipeline_crossings a ON a.event_id = k.anchor_event_id "
                "JOIN vahan v ON v.vrn = k.vrn WHERE k.case_id = ?", (case_id,)):
            if abs(c[3]) > half or (c[0] in best and abs(c[3]) >= abs(best[c[0]]["offset_minutes"])):
                continue
            best[c[0]] = {"vrn": c[0], "anchor_event_id": c[1], "plaza_id": c[2], "offset_minutes": round(c[3], 1),
                          "time": clock(c[4]), "registered": {"make": c[5], "model": c[6], "colour": c[7], "body": c[8]}}
        nearest = sorted(best.values(), key=lambda c: (abs(c["offset_minutes"]), c["vrn"]))[:ai_match.MAX_CANDIDATES]
        return sorted(nearest, key=lambda c: c["vrn"])     # display order carries no hint

    def frame(self, case_id: int, n: int) -> bytes | None:
        if not self.q("SELECT 1 FROM cases WHERE case_id = ?", (case_id,)):
            return None
        if n == 0:
            event_id = case_id
        else:
            lineup = self.vision_lineup(case_id)
            if not 1 <= n <= len(lineup):
                return None
            event_id = lineup[n - 1]["anchor_event_id"]
        with self.lock:
            return self.camera.frame(event_id, "GHOST" if n == 0 else f"CAND {n}")

    def vision_match(self, case_id: int) -> dict | None:
        if not self.q("SELECT 1 FROM cases WHERE case_id = ?", (case_id,)):
            return None
        if case_id in self.vision_results:
            return self.vision_results[case_id]
        lineup = self.vision_lineup(case_id)
        frames = [self.frame(case_id, n) for n in range(len(lineup) + 1)]
        status = self.q("SELECT plate_status FROM pipeline_crossings WHERE event_id = ?", (case_id,))[0][0]
        result = ai_match.match(frames[0], lineup, frames[1:], status)
        result["lineup"] = [{k: v for k, v in c.items() if k != "anchor_event_id"} | {"index": i}
                            for i, c in enumerate(lineup, 1)]
        if result["status"] == "ok":
            self.vision_results[case_id] = result
        return result

    def truth(self, case_id: int) -> dict | None:
        rows = self.q("SELECT v.vrn, v.make, v.model, v.colour, c.ghost_kind, "
                      "(SELECT GROUP_CONCAT(plaza_id) FROM crossings x WHERE x.truth_vehicle_id = v.vehicle_id "
                      " AND x.plaza_type = 'legacy' AND x.anpr_text IS NOT NULL) "
                      "FROM crossings c JOIN vehicles v ON v.vehicle_id = c.truth_vehicle_id WHERE c.event_id = ?", (case_id,))
        if not rows:
            return None
        r = rows[0]
        return {"vrn": r[0], "vehicle": f"{r[3]} {r[1]} {r[2]}", "kind": r[4],
                "identified_at": r[5].split(",") if r[5] else []}

    def review(self, case_id: int, body: dict) -> dict:
        decision = body.get("decision")
        if decision not in ("confirm", "reject", "need_more"):
            raise ValueError("decision must be confirm, reject or need_more")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        reviewer = str(body.get("reviewer") or "reviewer")[:60]
        with self.lock:
            if not self.con.execute("SELECT 1 FROM cases WHERE case_id = ?", (case_id,)).fetchone():
                raise KeyError(case_id)
            self.con.execute("UPDATE cases SET reviewer_decision = ?, reviewer_id = ?, reviewed_at = ? WHERE case_id = ?",
                             (decision, reviewer, now, case_id))
            self.con.execute("INSERT INTO audit_log(case_id, actor, action, ts, detail) VALUES (?,?,?,?,?)",
                             (case_id, reviewer, decision, now, str(body.get("note") or "")[:500]))
            self.con.commit()
        return {"ok": True, "case_id": case_id, "decision": decision, "at": now}

    def audit(self) -> list[dict]:
        return [{"case_id": r[0], "actor": r[1], "action": r[2], "at": r[3], "note": r[4]} for r in self.q(
            "SELECT case_id, actor, action, ts, detail FROM audit_log ORDER BY entry_id DESC LIMIT 100")]

    # ---- replay --------------------------------------------------------
    def replay(self, day: int, start_hour: float, minutes: float) -> dict:
        t0 = (day - 1) * 86400 + start_hour * 3600
        t1 = t0 + min(minutes, 240) * 60
        trips = {r[0]: {"w": [[round(r[1] - t0, 1), r[3]]], "end": [round(r[2] - t0, 1), r[4]], "d": r[5],
                        "c": speed_class_of(r[6])[0], "e": []}
                 for r in self.q("SELECT t.trip_id, t.start_ts, t.end_ts, t.start_km, t.end_km, t.direction, v.body "
                                 "FROM trips t JOIN vehicles v ON v.vehicle_id = t.vehicle_id "
                                 "WHERE t.end_ts >= ? AND t.start_ts <= ?", (t0, t1))}
        for r in self.q("SELECT c.trip_id, c.ts, c.plaza_id, c.system_class, k.decision, k.top_plaza_id, c.event_id "
                        "FROM crossings c JOIN trips t ON t.trip_id = c.trip_id LEFT JOIN cases k ON k.ghost_event_id = c.event_id "
                        "WHERE t.end_ts >= ? AND t.start_ts <= ? ORDER BY c.ts", (t0, t1)):
            trip = trips[r[0]]
            trip["w"].append([round(r[1] - t0, 1), self.km[r[2]]])
            if r[3] == "unidentified":
                trip["e"].append([round(r[1] - t0, 1), r[2], r[4], r[5], r[6]])
        out = []
        for t in trips.values():
            t["w"].append(t.pop("end"))
            out.append(t)
        return {"t0_label": clock(t0), "seconds": t1 - t0, "trips": out,
                "plazas": self.cfg["plazas"], "nodes_km": self.cfg["nodes_km"]}

    def summary(self) -> dict:
        return {"results": self.results, "settings": self.settings, "tuned": self.tuned,
                "windows": WINDOWS, "thresholds": THRESHOLDS, "presets": list(PRESETS),
                "camera": self.cfg["camera"], "plazas": self.cfg["plazas"],
                "counts": {r[0]: r[1] for r in self.q("SELECT decision, COUNT(*) FROM cases GROUP BY 1")}}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "GhostTrace/0"

        def log_message(self, fmt, *args):   # keep the console quiet
            pass

        def _json(self, obj, status: int = 200) -> None:
            data = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _png(self, data: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(data)

        def _static(self, path: str) -> None:
            target = (WEB / (path.lstrip("/") or "index.html")).resolve()
            if WEB.resolve() not in target.parents or not target.is_file():
                return self._json({"error": "not found"}, 404)
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            parts = [p for p in u.path.split("/") if p]
            try:
                if not parts or parts[0] != "api":
                    return self._static(u.path)
                route = parts[1] if len(parts) > 1 else ""
                if route == "health":
                    return self._json({"ok": True, "foundry_agent_enabled": is_foundry_agent_enabled(),
                                       "vision_ai_enabled": ai_match.is_enabled()})
                if route == "summary":
                    return self._json(app.summary())
                if route == "metrics":
                    return self._json(app.metrics(q.get("method", "full"), float(q.get("window", 10)),
                                                  round(float(q.get("threshold", 0.7)), 2), q.get("preset", "central")))
                if route == "curve":
                    return self._json(app.curve(q.get("method", "full"), float(q.get("window", 10)), q.get("preset", "central")))
                if route == "cases" and len(parts) == 2:
                    return self._json(app.case_list(q.get("decision") or None, min(int(q.get("limit", 50)), 200),
                                                    int(q.get("offset", 0))))
                if route == "cases" and len(parts) == 5 and parts[3] == "frames" and parts[4].endswith(".png"):
                    data = app.frame(int(parts[2]), int(parts[4][:-4]))
                    return self._png(data) if data else self._json({"error": "not found"}, 404)
                if route == "cases" and len(parts) >= 3:
                    obj = app.truth(int(parts[2])) if len(parts) == 4 and parts[3] == "truth" else app.case_detail(int(parts[2]))
                    return self._json(obj, 200) if obj else self._json({"error": "not found"}, 404)
                if route == "replay":
                    return self._json(app.replay(int(q.get("day", 2)), float(q.get("start_hour", 8)), float(q.get("minutes", 120))))
                if route == "audit":
                    return self._json(app.audit())
                return self._json({"error": "not found"}, 404)
            except (ValueError, KeyError) as exc:
                return self._json({"error": f"bad request: {exc}"}, 400)

        def do_POST(self) -> None:
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            try:
                if parts == ["v1", "evidence-pack"]:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                    case_id = int(body.get("case_id", 0)) if isinstance(body, dict) else 0
                    pack = app.evidence_pack(case_id)
                    return self._json(pack) if pack else self._json({"error": "case not found"}, 404)
                if parts == ["v1", "vision-match"]:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                    case_id = int(body.get("case_id", 0)) if isinstance(body, dict) else 0
                    result = app.vision_match(case_id)
                    return self._json(result) if result else self._json({"error": "case not found"}, 404)
                if len(parts) == 4 and parts[:2] == ["api", "cases"] and parts[3] == "review":
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                    return self._json(app.review(int(parts[2]), body))
                return self._json({"error": "not found"}, 404)
            except KeyError:
                return self._json({"error": "case not found"}, 404)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._json({"error": f"bad request: {exc}"}, 400)

    return Handler


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    app = App()
    print(f"[api] {len(app.test):,} test ghosts loaded. Open http://localhost:{port}")
    ThreadingHTTPServer((host, port), make_handler(app)).serve_forever()

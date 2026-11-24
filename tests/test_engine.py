"""The matching rule on hand-built cases. Run:  python -m unittest discover -s tests -v"""
import unittest

from services.common.db import connect
from services.correlate.engine import Candidate, build_pools, decide

CFG = {"plazas": [{"id": "L1", "type": "legacy", "km": 0}, {"id": "M1", "type": "mlff", "km": 45},
                  {"id": "L2", "type": "legacy", "km": 90}],
       "traffic": {"speed_kmph": {"car": [80, 100], "lcv": [55, 90], "truck": [40, 75]}},
       "matching": {"max_window_minutes": 60}}


def cand(vrn, offset, sim, plaza="L1"):
    return Candidate(vrn, plaza, offset, (sim, sim, sim))


class DecideRule(unittest.TestCase):
    def test_one_clear_candidate_is_recommended(self):
        d = decide([cand("A", 1, 0.85), cand("B", -2, 0.30)], 10, "full", 0.7, 0.1)
        self.assertEqual((d.decision, d.top.vrn, d.n_candidates), ("recommend", "A", 1))

    def test_two_lookalikes_go_to_review(self):
        d = decide([cand("A", 1, 0.85), cand("B", -2, 0.80)], 10, "full", 0.7, 0.1)
        self.assertEqual((d.decision, d.n_candidates), ("review", 2))

    def test_margin_too_small_goes_to_review(self):
        d = decide([cand("A", 1, 0.72), cand("B", -2, 0.68)], 10, "full", 0.7, 0.1)
        self.assertEqual(d.decision, "review")

    def test_candidate_outside_window_is_ignored(self):
        self.assertEqual(decide([cand("A", 8, 0.95)], 10, "full", 0.7).decision, "unresolved")
        self.assertEqual(decide([cand("A", 8, 0.95)], 20, "full", 0.7).decision, "recommend")

    def test_low_similarity_is_unresolved(self):
        self.assertEqual(decide([cand("A", 0, 0.4)], 10, "full", 0.7).decision, "unresolved")

    def test_more_than_five_passing_is_unresolved(self):
        pool = [cand(f"V{i}", 0, 0.9) for i in range(6)]
        self.assertEqual(decide(pool, 10, "full", 0.7).decision, "unresolved")

    def test_same_registration_at_two_plazas_counts_once(self):
        d = decide([cand("A", 1, 0.75, "L1"), cand("A", -1, 0.88, "L2")], 10, "full", 0.7, 0.1)
        self.assertEqual((d.decision, d.n_candidates, d.top.plaza_id), ("recommend", 1, "L2"))

    def test_reported_method_ignores_similarity(self):
        self.assertEqual(decide([cand("A", 1, 0.05)], 10, "reported").decision, "recommend")
        self.assertEqual(decide([cand("A", 1, 0.9), cand("B", 2, 0.1)], 10, "reported").decision, "review")
        self.assertEqual(decide([], 10, "reported").decision, "unresolved")


class FakeVision:
    def embed(self, event_id):
        return {p: [1.0, 0.0] for p in ("optimistic", "central", "pessimistic")}


class PoolGates(unittest.TestCase):
    """A ghost crosses M1 at t=3600 s heading towards L2 (45 km ahead; 30 minutes at 90 km/h)."""

    def setUp(self):
        self.con = connect(__import__("pathlib").Path(":memory:"), create=True)
        ins = "INSERT INTO crossings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        veh = "INSERT INTO vahan VALUES (?,?,?,?,?,?)"
        self.con.execute(ins, (1, "M1", "mlff", 1, 3600, None, None, "obstructed_deliberate",
                               "Kaveri", "Dart", "white", "hatchback", "unidentified", 1, 1, "deliberate"))
        rows = [  # event, plaza, direction, ts, vrn, colour
            (10, "L2", 1, 3600 + 31 * 60, "ONTIME", "white"),     # ahead, 1 minute late: in
            (11, "L2", 1, 3600 + 75 * 60, "LATE", "white"),       # 45 minutes late: outside the pool
            (12, "L2", -1, 3600 + 30 * 60, "WRONGWAY", "white"),  # opposite direction: out
            (13, "L2", 1, 3600 + 30 * 60, "RED", "red"),          # wrong colour: out
            (14, "L1", 1, 3600 - 29 * 60, "BEHIND", "white"),     # legacy plaza behind, before the ghost: in
            (15, "L1", 1, 3600 + 30 * 60, "L1AFTER", "white"),    # behind but after the ghost: out
        ]
        for e, plaza, direction, ts, vrn, colour in rows:
            self.con.execute(ins, (e, plaza, "legacy", direction, ts, "TAG", vrn, "readable",
                                   None, None, None, None, "normal", e, e, None))
            self.con.execute(veh, (vrn, "Kaveri", "Dart", colour, "hatchback", 2))
        self.con.commit()

    def test_time_direction_and_attribute_gates(self):
        self.assertEqual(build_pools(self.con, CFG, FakeVision()), 1)
        got = {r[0]: round(r[1], 1) for r in self.con.execute("SELECT vrn, offset_minutes FROM candidates")}
        self.assertEqual(got, {"ONTIME": 1.0, "BEHIND": 1.0})


if __name__ == "__main__":
    unittest.main()

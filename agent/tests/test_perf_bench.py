"""
perf_bench.py — verify the pure-function pieces. The bench_* functions
hit a live backend, so those are exercised by running the script
manually; this file only covers the maths + reporting.
"""
import unittest
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "perf_bench", Path(__file__).resolve().parents[2] / "scripts" / "perf_bench.py")
pb = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pb)  # type: ignore


class TestMs(unittest.TestCase):
    def test_round_to_one_decimal(self):
        self.assertEqual(pb._ms(0.123456), 123.5)
        self.assertEqual(pb._ms(0.0), 0.0)


class TestPercentile(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(pb._percentile([], 95), 0.0)

    def test_single(self):
        self.assertEqual(pb._percentile([0.5], 95), 0.5)

    def test_known(self):
        xs = [0.01, 0.02, 0.03, 0.04, 0.05]
        # p50 = 0.03, p95 ≈ 0.05
        self.assertEqual(pb._percentile(xs, 50), 0.03)
        self.assertEqual(pb._percentile(xs, 95), 0.05)


class TestReport(unittest.TestCase):
    def test_no_samples(self):
        r = pb.report("x", [], target_ms=100)
        self.assertEqual(r["n"], 0)
        self.assertIsNone(r["p50_ms"])
        self.assertEqual(r["verdict"], "no samples")

    def test_pass(self):
        # All samples well below 100ms
        r = pb.report("x", [0.01, 0.02, 0.03], target_ms=100)
        self.assertIn("pass", r["verdict"])
        self.assertEqual(r["n"], 3)
        self.assertLess(r["p95_ms"], 100)

    def test_miss(self):
        # p95 above target
        r = pb.report("x", [0.01, 0.5, 0.6, 0.7, 0.8], target_ms=100)
        self.assertIn("miss", r["verdict"])


if __name__ == "__main__":
    unittest.main()

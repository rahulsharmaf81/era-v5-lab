"""
alarms.py
=========
Rule-based alarm system for the ERA V5 Capstone Lab — Week 1.

Loads threshold rules from metric_list_v0.yaml (alarm_hi / alarm_lo fields),
then evaluates every (step, metric_name, value) observation against those
rules.  When a threshold is crossed, an AlarmEvent is created, printed,
and collected.

Usage
-----
    from alarms import AlarmSystem

    system = AlarmSystem()          # loads metric_list_v0.yaml automatically
    system.check(step, "b3_hit_rate", 0.95)   # fires if 0.95 > alarm_hi

    # --- or run the built-in 3 000-value stress test ---
    python alarms.py

The stress test generates 3,000 synthetic metric observations, feeds them
through the alarm system, and prints a summary table.
"""

from __future__ import annotations

import dataclasses
import datetime
import math
import pathlib
import random
import sys
from typing import Any

# ── Optional YAML import ─────────────────────────────────────────────────────

try:
    import yaml  # type: ignore
    _YAML_OK = True
except ImportError:
    _YAML_OK = False
    print("[alarms] PyYAML not installed — will use hard-coded thresholds.",
          file=sys.stderr)

# ── Paths ─────────────────────────────────────────────────────────────────────

_HERE = pathlib.Path(__file__).parent
_METRIC_YAML = _HERE / "metric_list_v0.yaml"

# ── Data classes ─────────────────────────────────────────────────────────────

@dataclasses.dataclass
class ThresholdRule:
    metric_name: str
    alarm_hi: float | None = None
    alarm_lo: float | None = None
    unit: str = ""
    description: str = ""


@dataclasses.dataclass
class AlarmEvent:
    ts: str
    step: int
    metric_name: str
    value: float
    threshold: float
    direction: str   # "HIGH" or "LOW"
    rule: ThresholdRule

    def __str__(self) -> str:
        arrow = "[HI]" if self.direction == "HIGH" else "[LO]"
        return (
            f"[ALARM {self.direction}] {arrow} "
            f"step={self.step:>4}  "
            f"{self.metric_name:<20} "
            f"value={self.value:>10.4f}  "
            f"threshold={self.threshold:>10.4f}"
        )


# ── Hard-coded fallback thresholds ────────────────────────────────────────────

_FALLBACK_RULES: list[dict[str, Any]] = [
    {"name": "b3_hit_rate",      "alarm_hi": 0.90},
    {"name": "b2_count",         "alarm_hi": 200},
    {"name": "b3_count",         "alarm_hi": 1000},
    {"name": "b4_count",         "alarm_hi": 100},
    {"name": "c2_count",         "alarm_hi": 200},
    {"name": "c2_hit_rate",      "alarm_hi": 0.40},
    {"name": "e3_count",         "alarm_hi": 5},
    {"name": "f1_count",         "alarm_hi": 200},
    {"name": "f1_hit_rate",      "alarm_hi": 0.30},
    {"name": "a2_count",         "alarm_hi": 500},
    {"name": "a4_count",         "alarm_hi": 50},
    {"name": "docs_dropped",     "alarm_hi": 10},
    {"name": "drop_rate",        "alarm_hi": 0.05},
    {"name": "b2_hit_rate",      "alarm_hi": 0.50},
    # example of a low-alarm: warn if processing stalls
    {"name": "tokens_processed", "alarm_lo": 100},
]


# ── AlarmSystem ───────────────────────────────────────────────────────────────

class AlarmSystem:
    """
    Evaluates metric observations against threshold rules loaded from
    metric_list_v0.yaml (or built-in fallbacks).

    Parameters
    ----------
    rules_path : path to metric_list_v0.yaml; defaults to sibling file
    verbose    : print each alarm event to stdout as it fires
    """

    def __init__(
        self,
        rules_path: str | pathlib.Path | None = None,
        verbose: bool = True,
    ) -> None:
        self.verbose = verbose
        self._rules: dict[str, ThresholdRule] = {}
        self.events: list[AlarmEvent] = []

        path = pathlib.Path(rules_path) if rules_path else _METRIC_YAML
        self._load_rules(path)

    # ── Public API ─────────────────────────────────────────────────────────────

    def check(self, step: int, metric_name: str, value: float) -> list[AlarmEvent]:
        """
        Evaluate one observation.

        Returns any AlarmEvent(s) that fired (may be empty).
        """
        fired: list[AlarmEvent] = []
        rule = self._rules.get(metric_name)
        if rule is None:
            return fired   # no rule for this metric — silently skip

        ts = datetime.datetime.utcnow().isoformat()

        if rule.alarm_hi is not None and value > rule.alarm_hi:
            evt = AlarmEvent(
                ts=ts, step=step, metric_name=metric_name,
                value=value, threshold=rule.alarm_hi,
                direction="HIGH", rule=rule,
            )
            fired.append(evt)

        if rule.alarm_lo is not None and value < rule.alarm_lo:
            evt = AlarmEvent(
                ts=ts, step=step, metric_name=metric_name,
                value=value, threshold=rule.alarm_lo,
                direction="LOW", rule=rule,
            )
            fired.append(evt)

        for evt in fired:
            self.events.append(evt)
            if self.verbose:
                print(evt)

        return fired

    def summary(self) -> None:
        """Print a grouped summary of all alarms fired so far."""
        if not self.events:
            print("[AlarmSystem] No alarms fired.")
            return

        print(f"\n{'='*65}")
        print(f"  ALARM SUMMARY -- {len(self.events)} events total")
        print(f"{'='*65}")

        # Group by metric_name
        by_metric: dict[str, list[AlarmEvent]] = {}
        for e in self.events:
            by_metric.setdefault(e.metric_name, []).append(e)

        print(f"{'Metric':<22} {'Direction':<8} {'Count':>6}  "
              f"{'Min value':>12}  {'Max value':>12}  {'Threshold':>12}")
        print("-" * 80)
        for metric, evts in sorted(by_metric.items()):
            values = [e.value for e in evts]
            thr    = evts[0].threshold
            dirn   = evts[0].direction
            print(f"{metric:<22} {dirn:<8} {len(evts):>6}  "
                  f"{min(values):>12.4f}  {max(values):>12.4f}  {thr:>12.4f}")
        print("=" * 80)

    # ── Private helpers ────────────────────────────────────────────────────────

    def _load_rules(self, path: pathlib.Path) -> None:
        """Load threshold rules from YAML, falling back to hard-coded list."""
        metrics_raw: list[dict] = []

        if _YAML_OK and path.exists():
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = yaml.safe_load(fh)
                metrics_raw = doc.get("metrics", [])
                print(f"[AlarmSystem] Loaded {len(metrics_raw)} rules "
                      f"from {path.name}")
            except Exception as exc:
                print(f"[AlarmSystem] Failed to parse YAML ({exc}); "
                      "using fallbacks.", file=sys.stderr)
                metrics_raw = _FALLBACK_RULES
        else:
            print("[AlarmSystem] metric_list_v0.yaml not found or PyYAML "
                  "unavailable -- using built-in fallbacks.")
            metrics_raw = _FALLBACK_RULES

        for m in metrics_raw:
            name = m.get("name")
            if not name:
                continue
            rule = ThresholdRule(
                metric_name=name,
                alarm_hi=m.get("alarm_hi"),
                alarm_lo=m.get("alarm_lo"),
                unit=m.get("unit", ""),
                description=m.get("description", ""),
            )
            if rule.alarm_hi is not None or rule.alarm_lo is not None:
                self._rules[name] = rule

        print(f"[AlarmSystem] {len(self._rules)} metrics have threshold rules.")


# ── Synthetic stress test (3 000 observations) ────────────────────────────────

def _generate_synthetic_observations(n: int = 3000) -> list[tuple[int, str, float]]:
    """
    Generate n synthetic (step, metric_name, value) tuples that exercise
    both normal and alarm-triggering ranges for several metrics.
    """
    rng = random.Random(42)
    metrics_config = [
        # (name,  normal_range,        spike_probability, spike_range)
        ("b3_hit_rate",      (0.70, 0.89),  0.12, (0.91, 1.00)),
        ("b2_count",         (50,   180),   0.10, (201,  350)),
        ("b3_count",         (200,  900),   0.10, (1001, 1500)),
        ("c2_hit_rate",      (0.10, 0.38),  0.08, (0.42, 0.65)),
        ("f1_count",         (10,   180),   0.09, (201,  400)),
        ("f1_hit_rate",      (0.05, 0.28),  0.07, (0.32, 0.55)),
        ("e3_count",         (0,    4),     0.05, (6,    20)),
        ("docs_dropped",     (0,    9),     0.06, (11,   30)),
        ("drop_rate",        (0.00, 0.04),  0.06, (0.06, 0.15)),
        ("a2_count",         (10,   490),   0.05, (501,  800)),
        ("a4_count",         (0,    45),    0.05, (51,   100)),
        ("tokens_processed", (500,  50000), 0.04, (10,   99)),   # LOW alarm
        ("b2_hit_rate",      (0.10, 0.48),  0.07, (0.52, 0.80)),
    ]

    obs: list[tuple[int, str, float]] = []
    per_metric = n // len(metrics_config)

    for name, normal, spike_prob, spike_range in metrics_config:
        for i in range(per_metric):
            step = i
            if rng.random() < spike_prob:
                lo, hi = spike_range
                value = rng.uniform(lo, hi)
            else:
                lo, hi = normal
                value = rng.uniform(lo, hi)
            # Round integer-ish metrics
            if "count" in name or name == "docs_dropped":
                value = round(value)
            obs.append((step, name, value))

    # pad to exactly n if needed
    while len(obs) < n:
        step, name, value = obs[rng.randint(0, len(obs) - 1)]
        obs.append((step + 1000, name, value))

    rng.shuffle(obs)
    return obs[:n]


def test_alarms(n: int = 3000) -> None:
    """
    Run the alarm system on n=3 000 synthetic observations and print a
    full summary.
    """
    print("=" * 65)
    print(f"  AlarmSystem stress test -- {n} synthetic observations")
    print("=" * 65)

    system = AlarmSystem(verbose=True)
    observations = _generate_synthetic_observations(n)

    print(f"\nFeeding {len(observations)} observations ...\n")
    for step, metric_name, value in observations:
        system.check(step, metric_name, value)

    system.summary()
    print(f"\nTotal observations : {n}")
    print(f"Total alarms fired : {len(system.events)}")
    print(f"Alarm rate         : {len(system.events)/n*100:.1f}%")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_alarms(3000)

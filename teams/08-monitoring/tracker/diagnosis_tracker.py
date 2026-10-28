"""
diagnosis_tracker.py
====================
Diagnosis Progress Tracker for ERA V5 Lab (Week 1).

Monitors and audits student submissions under diagnosis/submissions/:
- Validates 150-row milestone (Due: Tue 27 Oct, 23:59 IST)
- Validates all-rows milestone (Due: Sat 31 Oct, 23:59 IST)
- Validates medicine.md (Harm tests, Side effects, Actions)
- Scans for leaked personal data or secrets (conforms to pr-guard)
- Accepts a private roster file (CSV) without ever exposing emails
  to Git or generated reports.

Usage:
------
    # Scan existing submissions directly
    python diagnosis_tracker.py

    # Scan with a private roster file (external to git or local)
    python diagnosis_tracker.py --roster /path/to/private_roster.csv

    # Output markdown report to custom path
    python diagnosis_tracker.py --output-md tracker.md
"""

from __future__ import annotations

import argparse
import csv
import datetime
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Canonical Packs from pr-guard.yml ─────────────────────────────────────────

VALID_PACKS = [
    "cadj-commits", "cadj-notebooks", "cadj-stackexchange", "code-cpp", "code-go",
    "code-java", "code-js-ts", "code-python", "code-rust", "en-dclm",
    "en-fineweb-edu", "en-nemotroncc", "en-nemotroncc-synth", "ind-bn-fw2",
    "ind-bn-sangraha-v", "ind-gu-fw2", "ind-hi-fw2", "ind-hi-hplt",
    "ind-hi-indiccorp", "ind-hi-sangraha-s", "ind-hi-sangraha-v", "ind-kn-fw2",
    "ind-kn-sangraha-s", "ind-kn-sangraha-v", "ind-ml-fw2", "ind-mr-fw2",
    "ind-mr-sangraha-v", "ind-or-fw2", "ind-sa-fw2", "ind-ta-fw2",
    "ind-ta-sangraha-s", "ind-ta-sangraha-v", "ind-te-fw2", "ind-te-sangraha-s",
    "ind-te-sangraha-v", "math-finemath", "math-megamath", "sci-finepdfs"
]

# Sensitive pattern check (matching pr-guard.yml)
EMAIL_REGEX = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@"
    r"(?!users\.noreply\.github\.com\b|example\.(?:com|org)\b)"
    r"(?:[A-Za-z0-9-]+\.)+(?:com|org|net|edu|gov|mil|int|in|io|ai|co|uk|us|me|dev|app|info|biz|ac|ca|au|de|fr|jp|sg|ae)\b",
    re.IGNORECASE,
)
SECRET_REGEXES = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"), "GitHub token"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "API key"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
]


# ── Submission Audit Record ──────────────────────────────────────────────────

class StudentStatus:
    def __init__(self, pack_id: str, handle: str, name: str = "") -> None:
        self.pack_id = pack_id
        self.handle = handle
        self.name = name or handle
        self.labels_exists = False
        self.medicine_exists = False
        self.row_count = 0
        self.verdicts: Dict[str, int] = {}
        self.diseases_found: set[str] = set()
        self.harm_tests_count = 0
        self.leak_issues: List[str] = []

    @property
    def milestone_150_ok(self) -> bool:
        return self.row_count >= 150

    @property
    def milestone_all_ok(self) -> bool:
        # Typical slice is 500 rows
        return self.row_count >= 500

    @property
    def medicine_ok(self) -> bool:
        return self.medicine_exists and self.harm_tests_count >= 1

    @property
    def overall_status(self) -> str:
        if self.leak_issues:
            return "LEAK_DETECTED"
        if self.milestone_all_ok and self.medicine_ok:
            return "COMPLETE"
        if self.milestone_150_ok:
            return "IN_PROGRESS_150+"
        if self.labels_exists:
            return "STARTED"
        return "PENDING"


# ── Audit Engine ─────────────────────────────────────────────────────────────

def audit_submission(repo_root: Path, pack_id: str, handle: str, name: str = "") -> StudentStatus:
    status = StudentStatus(pack_id=pack_id, handle=handle, name=name)
    sub_dir = repo_root / "diagnosis" / "submissions" / pack_id / handle

    if not sub_dir.exists():
        return status

    labels_file = sub_dir / "labels.csv"
    medicine_file = sub_dir / "medicine.md"

    # 1. Audit labels.csv
    if labels_file.exists():
        status.labels_exists = True
        try:
            with open(labels_file, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                status.row_count = len(rows)
                for r in rows:
                    v = (r.get("verdict") or "").strip().lower()
                    if v:
                        status.verdicts[v] = status.verdicts.get(v, 0) + 1
                    dis = (r.get("diseases") or "").strip()
                    if dis and dis.lower() != "none":
                        for d in re.split(r"[\s,;]+", dis):
                            if d:
                                status.diseases_found.add(d.upper())

                    # Check for secrets/personal data leaks in evidence or notes
                    evidence = r.get("evidence", "")
                    note = r.get("note", "")
                    for text in (evidence, note):
                        if EMAIL_REGEX.search(text):
                            status.leak_issues.append(f"Email leaked in labels.csv: {text[:40]}...")
                        for reg, what in SECRET_REGEXES:
                            if reg.search(text):
                                status.leak_issues.append(f"{what} leaked in labels.csv")
        except Exception as e:
            status.leak_issues.append(f"Error parsing labels.csv: {e}")

    # 2. Audit medicine.md
    if medicine_file.exists():
        status.medicine_exists = True
        try:
            content = medicine_file.read_text(encoding="utf-8", errors="replace")
            # Count harm tests
            ht_matches = re.findall(r"harm\s*test", content, re.IGNORECASE)
            status.harm_tests_count = len(ht_matches)

            # Check privacy leaks
            if EMAIL_REGEX.search(content):
                status.leak_issues.append("Email pattern leaked in medicine.md")
            for reg, what in SECRET_REGEXES:
                if reg.search(content):
                    status.leak_issues.append(f"{what} pattern leaked in medicine.md")
        except Exception as e:
            status.leak_issues.append(f"Error reading medicine.md: {e}")

    return status


def load_roster(roster_path: Path) -> List[Tuple[str, str, str]]:
    """
    Load private roster CSV: name, handle, pack_id (and optionally email).
    STRIPS OUT AND DISCARDS EMAIL IMMEDIATELY.
    Returns: list of (pack_id, handle, name)
    """
    results: List[Tuple[str, str, str]] = []
    if not roster_path.exists():
        print(f"[Tracker] Roster file not found: {roster_path}", file=sys.stderr)
        return results

    with open(roster_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Case-insensitive column lookup
            keys = {k.lower().strip(): k for k in row.keys()}
            pack_key = keys.get("pack") or keys.get("pack_id") or keys.get("packid")
            handle_key = keys.get("handle") or keys.get("github") or keys.get("github_handle") or keys.get("username")
            name_key = keys.get("name") or keys.get("student_name") or keys.get("student")

            pack_id = row[pack_key].strip() if pack_key else ""
            handle = row[handle_key].strip() if handle_key else ""
            name = row[name_key].strip() if name_key else handle

            if pack_id and handle:
                results.append((pack_id, handle, name))

    print(f"[Tracker] Loaded {len(results)} student records from private roster (emails stripped).")
    return results


def discover_submissions(repo_root: Path) -> List[Tuple[str, str, str]]:
    """Scan diagnosis/submissions directory for existing students."""
    sub_root = repo_root / "diagnosis" / "submissions"
    results = []
    if not sub_root.exists():
        return results

    for pack_dir in sub_root.iterdir():
        if pack_dir.is_dir() and not pack_dir.name.startswith("."):
            pack_id = pack_dir.name
            for student_dir in pack_dir.iterdir():
                if student_dir.is_dir() and not student_dir.name.startswith("."):
                    handle = student_dir.name
                    results.append((pack_id, handle, handle))
    return results


def generate_markdown_report(records: List[StudentStatus], output_path: Path) -> None:
    now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# Diagnosis Tracker (Week 1)",
        "",
        f"*Generated: {now_str}*",
        "",
        "> **Privacy Notice:** Strictly compliant with lab policy. No personal email addresses are ever stored or written to git.",
        "",
        "## Submission Status Table",
        "",
        "| Pack ID | Student | GitHub Handle | Rows | Oct 27 (150+) | Oct 31 (All) | Medicine | Status |",
        "|---|---|---|:---:|:---:|:---:|:---:|:---:|",
    ]

    for s in records:
        m150 = "PASS" if s.milestone_150_ok else "MISSING"
        mall = "PASS" if s.milestone_all_ok else "PENDING"
        med = f"PASS ({s.harm_tests_count} HT)" if s.medicine_ok else ("NO_HT" if s.medicine_exists else "MISSING")
        lines.append(
            f"| `{s.pack_id}` | {s.name} | `{s.handle}` | {s.row_count} | {m150} | {mall} | {med} | **{s.overall_status}** |"
        )

    lines.extend([
        "",
        "## Summary Metrics",
        "",
        f"- **Total Tracked Submissions:** {len(records)}",
        f"- **Completed (All Rows + Medicine):** {sum(1 for s in records if s.overall_status == 'COMPLETE')}",
        f"- **Met Oct 27 Milestone (150+ rows):** {sum(1 for s in records if s.milestone_150_ok)}",
        f"- **Started:** {sum(1 for s in records if s.labels_exists)}",
        f"- **Security / Privacy Violations:** {sum(len(s.leak_issues) for s in records)}",
        "",
    ])

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[Tracker] Clean markdown report saved -> {output_path}")


def print_terminal_summary(records: List[StudentStatus]) -> None:
    print("\n" + "=" * 80)
    print("  ERA V5 DATA DIAGNOSIS TRACKER -- WEEK 1 STATUS")
    print("=" * 80)
    print(f"{'Pack ID':<22} {'Handle':<16} {'Rows':>6}  {'Oct27':>7}  {'Oct31':>7}  {'Medicine':>10}  {'Status':<14}")
    print("-" * 80)
    for s in records:
        m150 = "PASS" if s.milestone_150_ok else "FAIL"
        mall = "PASS" if s.milestone_all_ok else "PENDING"
        med = "PASS" if s.medicine_ok else "FAIL"
        print(f"{s.pack_id:<22} {s.handle:<16} {s.row_count:>6}  {m150:>7}  {mall:>7}  {med:>10}  {s.overall_status:<14}")
    print("=" * 80)


def main() -> None:
    parser = argparse.ArgumentParser(description="ERA V5 Diagnosis Tracker")
    parser.add_argument("--repo-root", default=".", help="Root path of era-v5-lab repository")
    parser.add_argument("--roster", default="", help="Path to private roster CSV (not in git)")
    parser.add_argument("--output-md", default="", help="Path for output markdown report")
    parser.add_argument("--create-sample-roster", default="", help="Generate an external sample roster file")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()

    # Generate sample roster if requested
    if args.create_sample_roster:
        sample_path = Path(args.create_sample_roster).resolve()
        with open(sample_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["student_name", "email", "github_handle", "pack_id"])
            writer.writerow(["Rahul Sharma", "example.student@domain.invalid", "rahulsharmaf81", "cadj-stackexchange"])
            writer.writerow(["Peer Reviewer 1", "peer1@domain.invalid", "peer1", "cadj-commits"])
        print(f"[Tracker] Sample private roster generated at: {sample_path}")
        return

    # Collect entries from roster or filesystem
    entries: List[Tuple[str, str, str]] = []
    if args.roster:
        entries = load_roster(Path(args.roster))

    if not entries:
        print("[Tracker] Scanning diagnosis/submissions directly...")
        entries = discover_submissions(repo_root)

    if not entries:
        print("[Tracker] No submissions or roster records found.")
        return

    # Audit each student
    records = [
        audit_submission(repo_root, pack_id, handle, name)
        for pack_id, handle, name in entries
    ]

    print_terminal_summary(records)

    # Determine report path
    out_path = Path(args.output_md) if args.output_md else (repo_root / "teams" / "08-monitoring" / "tracker" / "tracker.md")
    generate_markdown_report(records, out_path)


if __name__ == "__main__":
    main()

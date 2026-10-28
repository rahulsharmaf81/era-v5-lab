"""
metrics_writer.py
=================
Buffered metric writer for the ERA V5 Capstone Lab — Week 1.

Writes every recorded (step, metric, value) tuple to ClickHouse when
clickhouse-driver is available; otherwise falls back to a local
newline-delimited JSON file (metrics.ndjson) in the same directory.

Flush policy: buffer is drained automatically every FLUSH_EVERY steps.
Call writer.flush() explicitly to drain at any other time.

Usage example
-------------
    from metrics_writer import MetricsWriter

    writer = MetricsWriter(pack_id="cadj-stackexchange",
                           submitter="rahulsharmaf81")
    for step in range(300):
        writer.record(step, "b3_hit_rate", 0.98)
        writer.record(step, "docs_total",  step + 1)
        # flush happens automatically every 10 steps

    writer.flush()   # final drain
    writer.close()
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

FLUSH_EVERY: int = 10          # flush buffer after this many steps
FALLBACK_FILE: str = "metrics.ndjson"

# ── ClickHouse helpers ───────────────────────────────────────────────────────

def _try_import_clickhouse():
    """Return the clickhouse_driver Client class or None."""
    try:
        from clickhouse_driver import Client  # type: ignore
        return Client
    except ImportError:
        return None


def _make_clickhouse_client(host: str, port: int, database: str, table: str):
    """
    Connect to ClickHouse and ensure the target table exists.

    CREATE TABLE IF NOT EXISTS <table> (
        ts           DateTime,
        pack_id      String,
        submitter    String,
        step         UInt32,
        metric_name  String,
        value        Float64
    ) ENGINE = MergeTree()
    ORDER BY (pack_id, step, metric_name);
    """
    Client = _try_import_clickhouse()
    if Client is None:
        return None
    try:
        client = Client(host=host, port=port, database=database)
        client.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                ts           DateTime,
                pack_id      String,
                submitter    String,
                step         UInt32,
                metric_name  String,
                value        Float64
            ) ENGINE = MergeTree()
            ORDER BY (pack_id, step, metric_name)
            """
        )
        return client
    except Exception as exc:
        print(f"[MetricsWriter] ClickHouse connection failed ({exc}); "
              "falling back to local NDJSON.", file=sys.stderr)
        return None


# ── MetricsWriter ────────────────────────────────────────────────────────────

class MetricsWriter:
    """
    Buffers (step, metric_name, value) tuples and flushes every FLUSH_EVERY
    steps — either to ClickHouse or to a local NDJSON file.

    Parameters
    ----------
    pack_id    : identifier of the data pack being diagnosed
    submitter  : student identifier (no email)
    flush_every: override the default flush cadence
    output_dir : directory for the fallback NDJSON file
    ch_host    : ClickHouse host   (default: localhost)
    ch_port    : ClickHouse port   (default: 9000)
    ch_database: ClickHouse DB     (default: default)
    ch_table   : ClickHouse table  (default: diagnosis_metrics)
    """

    def __init__(
        self,
        pack_id: str = "cadj-stackexchange",
        submitter: str = "rahulsharmaf81",
        flush_every: int = FLUSH_EVERY,
        output_dir: str | Path | None = None,
        ch_host: str = "localhost",
        ch_port: int = 9000,
        ch_database: str = "default",
        ch_table: str = "diagnosis_metrics",
    ) -> None:
        self.pack_id = pack_id
        self.submitter = submitter
        self.flush_every = flush_every
        self._buffer: list[dict[str, Any]] = []
        self._last_flushed_step: int = -1

        # Output directory (fallback path)
        if output_dir is None:
            output_dir = Path(__file__).parent
        self._output_dir = Path(output_dir)
        self._ndjson_path = self._output_dir / FALLBACK_FILE

        # Try ClickHouse
        self._ch_client = _make_clickhouse_client(
            ch_host, ch_port, ch_database, ch_table
        )
        self._ch_table = ch_table

        if self._ch_client:
            print(f"[MetricsWriter] Connected to ClickHouse — "
                  f"{ch_host}:{ch_port}/{ch_database}.{ch_table}")
        else:
            print(f"[MetricsWriter] Using local fallback -> {self._ndjson_path}")

    # ── Public API ────────────────────────────────────────────────────────────

    def record(self, step: int, metric_name: str, value: float) -> None:
        """
        Buffer one metric observation.

        Auto-flushes when the step crosses a multiple of flush_every
        (i.e. the buffer is drained once per flush window, not per step).
        """
        row = {
            "ts": datetime.datetime.utcnow().isoformat(),
            "pack_id": self.pack_id,
            "submitter": self.submitter,
            "step": step,
            "metric_name": metric_name,
            "value": float(value),
        }
        self._buffer.append(row)

        # Flush when we cross a flush boundary for the first time
        flush_window = step // self.flush_every
        if flush_window > self._last_flushed_step // max(self.flush_every, 1):
            self.flush()
            self._last_flushed_step = step

    def flush(self) -> int:
        """
        Drain the buffer immediately.

        Returns the number of rows flushed.
        """
        if not self._buffer:
            return 0

        rows = self._buffer[:]
        self._buffer.clear()

        if self._ch_client:
            self._flush_clickhouse(rows)
        else:
            self._flush_ndjson(rows)

        print(f"[MetricsWriter] Flushed {len(rows)} rows "
              f"(step={rows[-1]['step']})")
        return len(rows)

    def close(self) -> None:
        """Flush any remaining rows and release resources."""
        self.flush()
        if self._ch_client:
            try:
                self._ch_client.disconnect()
            except Exception:
                pass
        print("[MetricsWriter] Closed.")

    # ── Context-manager support ───────────────────────────────────────────────

    def __enter__(self) -> "MetricsWriter":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _flush_clickhouse(self, rows: list[dict]) -> None:
        """Insert a batch of rows into ClickHouse."""
        data = [
            (
                datetime.datetime.fromisoformat(r["ts"]),
                r["pack_id"],
                r["submitter"],
                r["step"],
                r["metric_name"],
                r["value"],
            )
            for r in rows
        ]
        try:
            self._ch_client.execute(
                f"INSERT INTO {self._ch_table} "
                "(ts, pack_id, submitter, step, metric_name, value) VALUES",
                data,
            )
        except Exception as exc:
            print(f"[MetricsWriter] ClickHouse insert failed ({exc}); "
                  "writing to fallback NDJSON instead.", file=sys.stderr)
            self._flush_ndjson(rows)

    def _flush_ndjson(self, rows: list[dict]) -> None:
        """Append rows to the local NDJSON fallback file."""
        with open(self._ndjson_path, "a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")


# ── CLI smoke-test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import random

    print("=" * 60)
    print("MetricsWriter smoke-test: 50 steps, flush every 10")
    print("=" * 60)

    writer = MetricsWriter(
        pack_id="cadj-stackexchange",
        submitter="rahulsharmaf81",
        flush_every=10,
    )

    for step in range(50):
        writer.record(step, "b3_hit_rate",   random.uniform(0.90, 1.00))
        writer.record(step, "b2_count",      random.randint(0, 5))
        writer.record(step, "docs_total",    step + 1)
        writer.record(step, "tokens_processed", (step + 1) * random.randint(50, 300))

    writer.close()

    # Show what landed in the fallback file
    fallback = Path(__file__).parent / FALLBACK_FILE
    if fallback.exists():
        lines = fallback.read_text(encoding="utf-8").splitlines()
        print(f"\nFallback NDJSON written: {len(lines)} rows in {fallback}")
        print("First 3 rows:")
        for line in lines[:3]:
            print(" ", line)

"""CLI:  python -m pm_simulator replay --scenario test --engines 24,57 --interval 1.0

Exit codes: 0 finished (or dry run) | 1 error | 2 bad arguments | 130 interrupted (progress is saved; re-run to resume)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Callable

import httpx

from pm_simulator.replay import (
    DEFAULT_OFFSETS, ApiClient, ReplayError, build_plan, format_event, format_summary, load_api_key, run_replay,
    safe_url,
)

DEFAULT_API_URL = "http://127.0.0.1:8000"


def parse_units(spec: str) -> list[int]:
    """'24' | '1,5,7' | '1-3,24' -> ordered unique list. Raises ValueError on bad input."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty item in engine list")
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
            if lo > hi:
                raise ValueError(f"bad range '{part}'")
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(part))
    if any(u < 1 for u in out):
        raise ValueError("engine numbers must be >= 1")
    if len(set(out)) != len(out):
        raise ValueError("engine list contains duplicates")
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pm_simulator", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("replay", help="replay historical FD001 readings through the ingestion API")
    r.add_argument("--scenario", choices=sorted(DEFAULT_OFFSETS), required=True,
                   help="test: engines cut before failure (no per-cycle truth); train: run-to-failure (truth known)")
    r.add_argument("--engines", required=True, help="source engine numbers, e.g. 24 or 1,5,7 or 1-3,24")
    r.add_argument("--id-offset", type=int, default=None,
                   help=f"backend engine id = unit + offset (default: {DEFAULT_OFFSETS})")
    r.add_argument("--interval", type=float, default=1.0, help="seconds between ticks; 0 = as fast as possible")
    r.add_argument("--max-cycles", type=int, default=None, help="send at most N cycles per engine in this run")
    r.add_argument("--api-url", default=os.environ.get("PM_API_URL", DEFAULT_API_URL))
    r.add_argument("--raw-dir", default=None, help="folder with the NASA FD001 files (default: ml/data/raw)")
    r.add_argument("--env-file", default=None, help="file to read API_KEY from if the env var is unset")
    r.add_argument("--timeout", type=float, default=10.0, help="per-request timeout in seconds")
    r.add_argument("--dry-run", action="store_true", help="show the plan; send nothing")
    return p


def main(argv: list[str] | None = None, *, http_factory: Callable[[str, float], httpx.Client] | None = None,
         out: Callable[[str], None] = print, sleep=None) -> int:
    parser = build_parser()
    try:
        a = parser.parse_args(argv)
    except SystemExit as e:  # argparse usage error -> 2
        return int(e.code or 0)
    try:
        units = parse_units(a.engines)
    except ValueError as e:
        parser.print_usage(sys.stderr)
        print(f"error: --engines: {e}", file=sys.stderr)
        return 2
    if a.interval < 0 or (a.max_cycles is not None and a.max_cycles < 1) or a.timeout <= 0:
        print("error: --interval must be >= 0, --max-cycles >= 1, --timeout > 0", file=sys.stderr)
        return 2
    try:
        plan = build_plan(a.scenario, units, a.id_offset, **({"raw_dir": a.raw_dir} if a.raw_dir else {}))
        out(f"SIMULATED replay of NASA C-MAPSS FD001 '{a.scenario}' data (historical, not real aircraft) -> "
            f"{safe_url(a.api_url)}")
        for s in plan:
            out(f"  unit {s.source_unit} -> backend engine id {s.engine_id} ({s.n_cycles} cycles)")
        if a.dry_run:
            out("dry run: nothing was sent.")
            return 0
        key = load_api_key(env_file=Path(a.env_file) if a.env_file else None)
        http = (http_factory or (lambda url, t: httpx.Client(base_url=url, timeout=t)))(a.api_url, a.timeout)
        client_kw = {"sleep": sleep} if sleep else {}
        client = ApiClient(http, key, **client_kw)
        client.check_ready()
        result = run_replay(plan, client, interval=a.interval, max_cycles=a.max_cycles, on_event=lambda e: out(format_event(e)),
                            on_note=lambda m: out(f"note: {m}"), **({"sleep": sleep} if sleep else {}))
    except ReplayError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    out(format_summary(result))
    return 130 if result.interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())

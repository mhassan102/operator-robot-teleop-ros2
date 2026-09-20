"""ICE agent CLI. T3: `python3 -m agent gather --config config/local.yaml`."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .ice import format_candidate, gather, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ICE agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gather", help="STUN gather on the configured NIC")
    g.add_argument("--config", required=True, help="path to YAML (e.g. config/local.yaml)")
    args = parser.parse_args(argv)
    if args.cmd != "gather":
        parser.error("T3 CLI is gather only")

    cfg = load_config(Path(args.config))
    cands, note = asyncio.run(gather(cfg))
    ifname = cfg.get("ifname") or "-"
    stun = cfg.get("stun_server") or "-"
    print(
        f"bind_ip={cfg['bind_ip']} ifname={ifname} "
        f"stun={stun}:{cfg.get('stun_port', '-')}"
    )
    if note:
        print(f"note: {note}", file=sys.stderr)
    for c in cands:
        print(format_candidate(c))
    srflx = [c for c in cands if c.get("type") == "srflx"]
    if not cands:
        print("no candidates", file=sys.stderr)
        return 1
    if not srflx:
        print("no srflx (STUN timeout or unreachable)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

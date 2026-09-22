"""ICE agent CLI.

T3: `python3 -m agent gather --config config/local.yaml`
T4/T5: `python3 -m agent run --config config/local.yaml`
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .ice import format_candidate, gather, ice_policy, load_config, turn_params
from .session import run_session


def _cmd_gather(cfg: dict) -> int:
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
    relay = [c for c in cands if c.get("type") == "relay"]
    if not cands:
        print("no candidates", file=sys.stderr)
        return 1
    policy = ice_policy(cfg)
    if policy == "relay":
        if not relay:
            print(
                "no relay candidate (TURN allocate failed or timed out)",
                file=sys.stderr,
            )
            return 1
        return 0
    if not srflx:
        print("no srflx (STUN timeout or unreachable)", file=sys.stderr)
        return 1
    if turn_params(cfg) is not None and not relay:
        print(
            "warning: no relay candidate (TURN allocate failed or timed out)",
            file=sys.stderr,
        )
    return 0


def _cmd_run(cfg: dict) -> int:
    result = asyncio.run(run_session(cfg, hold=True))
    return 0 if result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ICE agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gather", help="STUN gather on the configured NIC")
    g.add_argument("--config", required=True, help="path to YAML (e.g. config/local.yaml)")
    r = sub.add_parser("run", help="gather, signal, punch, nominate, echo hello")
    r.add_argument("--config", required=True, help="path to YAML (e.g. config/local.yaml)")
    args = parser.parse_args(argv)

    cfg = load_config(Path(args.config))
    if args.cmd == "gather":
        return _cmd_gather(cfg)
    if args.cmd == "run":
        return _cmd_run(cfg)
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

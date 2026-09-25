"""ICE agent CLI.

T3: `python3 -m agent gather --config config/local.yaml`
T4/T5: `python3 -m agent run --config config/local.yaml`
T6: `python3 -m agent nat`, `nat-ttl`, `stun-respond`
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .ice import format_candidate, gather, ice_policy, load_config, turn_params
from .nat import nat_check, parse_holds, run_holds, run_refresh, run_stun_respond
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


def _guard(fn) -> int:
    try:
        return fn()
    except (TimeoutError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _cmd_nat(cfg: dict, listen_s: int) -> int:
    return _guard(lambda: nat_check(cfg, listen_s))


def _cmd_nat_ttl(cfg: dict, args: argparse.Namespace) -> int:
    refresh = args.refresh
    duration = args.duration
    holds = args.holds
    if refresh is not None or duration is not None:
        if holds is not None:
            print(
                "nat-ttl --refresh/--duration cannot be combined with --holds",
                file=sys.stderr,
            )
            return 2
        if refresh is None or duration is None:
            print("nat-ttl refresh soak needs --refresh and --duration", file=sys.stderr)
            return 2
        if refresh < 1 or duration < 1:
            print("nat-ttl --refresh and --duration must be >= 1 second", file=sys.stderr)
            return 2
        return _guard(lambda: run_refresh(cfg, refresh, duration))
    if not holds:
        print("nat-ttl needs --holds or both --refresh and --duration", file=sys.stderr)
        return 2
    try:
        parsed = parse_holds(holds)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return _guard(lambda: run_holds(cfg, parsed))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ICE agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gather", help="STUN gather on the configured NIC")
    g.add_argument("--config", required=True, help="path to YAML (e.g. config/local.yaml)")
    r = sub.add_parser("run", help="gather, signal, punch, nominate, echo hello")
    r.add_argument("--config", required=True, help="path to YAML (e.g. config/local.yaml)")
    n = sub.add_parser("nat", help="classify NAT from two STUN Bindings on one socket")
    n.add_argument("--config", required=True, help="path to YAML (e.g. config/local.yaml)")
    n.add_argument(
        "--listen-s",
        type=int,
        default=0,
        help="after the class line, wait N seconds for one non-STUN datagram",
    )
    t = sub.add_parser("nat-ttl", help="manual mapping lifetime holds, or a refresh soak")
    t.add_argument("--config", required=True, help="path to YAML (e.g. config/local.yaml)")
    t.add_argument("--holds", help="comma-separated idle seconds, one fresh socket each")
    t.add_argument("--refresh", type=int, help="STUN Binding interval for the soak")
    t.add_argument("--duration", type=int, help="soak length in seconds")
    s = sub.add_parser("stun-respond", help="test Binding responder (not coturn)")
    s.add_argument("--bind", default="0.0.0.0", help="local address (not 100.x)")
    s.add_argument("--port", type=int, required=True, help="UDP port, not 3478 or 50000-50100")
    args = parser.parse_args(argv)

    if args.cmd == "stun-respond":
        return run_stun_respond(args.bind, args.port)

    cfg = load_config(Path(args.config))
    if args.cmd == "gather":
        return _cmd_gather(cfg)
    if args.cmd == "run":
        return _cmd_run(cfg)
    if args.cmd == "nat":
        return _cmd_nat(cfg, args.listen_s)
    if args.cmd == "nat-ttl":
        return _cmd_nat_ttl(cfg, args)
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

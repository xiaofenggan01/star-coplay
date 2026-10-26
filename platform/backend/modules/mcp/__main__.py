"""Executable StarCoPlay MCP profile entrypoint."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# ``modules`` lives below platform/backend while the canonical protocol mapper
# lives at the repository root.  Make the executable module self-contained
# when launched from platform/backend without requiring a caller-owned
# PYTHONPATH convention.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from modules.mcp.profiles.local_stdio import (
    LocalDirectStdioConfig,
    default_repo_root,
    run_local_direct_stdio,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m modules.mcp")
    parser.add_argument("--profile", required=True, choices=("local_direct",))
    parser.add_argument("--readiness-timeout", type=float, default=10.0)
    parser.add_argument("--boot-lifetime", type=float, default=300.0)
    parser.add_argument("--rendezvous-path", type=Path)
    parser.add_argument(
        "--enable-controlled-mutations",
        action="store_true",
        help="allow the audited Local controlled Skill samples to change the save",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.readiness_timeout <= 0:
        build_parser().error("--readiness-timeout must be positive")
    if args.boot_lifetime <= 0:
        build_parser().error("--boot-lifetime must be positive")
    config = LocalDirectStdioConfig(
        repo_root=default_repo_root(),
        rendezvous_path=args.rendezvous_path,
        readiness_timeout_seconds=args.readiness_timeout,
        boot_lifetime_seconds=args.boot_lifetime,
        enable_controlled_mutations=args.enable_controlled_mutations,
    )
    try:
        asyncio.run(run_local_direct_stdio(config))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"local_direct startup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

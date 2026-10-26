from __future__ import annotations

import argparse
import json
from pathlib import Path

from modules.mcp.manifest import baseline_document


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(baseline_document(args.repo_root), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()


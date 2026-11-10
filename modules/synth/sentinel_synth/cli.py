"""Command line: generate a data set, or describe one."""

import argparse
import json
import sys
import time
from pathlib import Path

from sentinel_synth.config import PROFILES, profile
from sentinel_synth.generator import generate
from sentinel_synth.writer import write


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sentinel-synth", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="write a synthetic data set")
    gen.add_argument("--profile", choices=sorted(PROFILES), default="demo")
    gen.add_argument("--institution", choices=["urban_cooperative_bank", "nbfc"])
    gen.add_argument("--seed", type=int)
    gen.add_argument("--customers", type=int)
    gen.add_argument("--days", type=int)
    gen.add_argument("--out", type=Path, required=True)
    describe = sub.add_parser("describe", help="print the manifest summary of a data set")
    describe.add_argument("directory", type=Path)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if args.command == "describe":
        manifest = json.loads((args.directory / "manifest.json").read_text(encoding="utf-8"))
        print(json.dumps({"config": manifest["config"], "stats": manifest["stats"]}, indent=2))
        return
    overrides = {
        name: value
        for name in ("institution", "seed", "customers", "days")
        if (value := getattr(args, name)) is not None
    }
    started = time.perf_counter()
    world = generate(profile(args.profile, **overrides))
    manifest = write(world, args.out)
    elapsed = time.perf_counter() - started
    summary = manifest["stats"]
    print(
        f"{summary['parties']} parties, {summary['accounts']} accounts, {summary['loans']} loans, "
        f"{summary['transactions']} transactions ({summary['fraud_transactions']} in "
        f"{summary['fraud_scenarios']} fraud scenarios) written to {args.out} in {elapsed:.1f}s"
    )

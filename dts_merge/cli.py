# dts-merge - Interactive merge of a generated FPGA-fabric DTS into an HPS/kernel devicetree
#
# Copyright (C) 2026 Laurence <laurence@anodes4life.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Headless CLI — the GUI in fpga-embedded-studio is the primary interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .merge import MergeError, Resolution, merge_trees
from .parser import DTSParseError, preprocess_and_parse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dts-merge",
        description="Graft a generated FPGA-fabric DTS onto an HPS/kernel devicetree.",
    )
    p.add_argument("--hps", required=True, metavar="file.dts", help="HPS/kernel-side DTS/DTSI file")
    p.add_argument("--fpga", required=True, metavar="file.dts", help="FPGA-fabric-side DTS file (e.g. sopc2dts output)")
    p.add_argument("-I", "--include", dest="include_dirs", action="append", default=[],
                    metavar="dir", help="Extra #include search directory (can repeat)")
    p.add_argument("--anchor", dest="anchor_label", metavar="label", default=None,
                    help="Label in the HPS tree to graft the fpga tree onto (default: HPS root)")
    p.add_argument("--fpga-anchor", dest="fpga_anchor_label", metavar="label", default=None,
                    help="Label inside the fpga tree whose children get grafted (default: fpga root)")
    p.add_argument("--auto", choices=["base", "fpga"], default=None,
                    help="Resolve every conflict automatically (headless use). "
                         "Without this, any unresolved conflict aborts with a non-zero exit code.")
    p.add_argument("-o", "--output", metavar="merged.dts", help="Output file (default: stdout)")
    p.add_argument("--version", action="version", version=f"dts-merge {__version__}")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    include_dirs = [Path(d) for d in args.include_dirs]

    try:
        hps = preprocess_and_parse(Path(args.hps), include_dirs)
        fpga = preprocess_and_parse(Path(args.fpga), include_dirs)
    except DTSParseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        result = merge_trees(
            hps.root,
            fpga.root,
            base_anchor_label=args.anchor_label,
            fpga_anchor_label=args.fpga_anchor_label,
            unresolved_fpga_amendments=fpga.unresolved_amendments,
        )
    except MergeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.auto:
        resolution = Resolution.BASE if args.auto == "base" else Resolution.FPGA
        for i, conflict in enumerate(result.conflicts):
            result.resolve(i, resolution)

    if result.unresolved:
        print(f"{len(result.unresolved)} unresolved conflict(s):", file=sys.stderr)
        for c in result.unresolved:
            print(f"  [{c.kind.name}] {c.path}", file=sys.stderr)
        print("Re-run with --auto base|fpga, or resolve interactively via the GUI.", file=sys.stderr)
        sys.exit(2)

    output = "/dts-v1/;\n" + result.merged_root.to_string(0)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)


if __name__ == "__main__":
    main()

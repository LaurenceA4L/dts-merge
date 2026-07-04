# dts-merge

Interactively merge a generated FPGA-fabric DTS fragment (e.g. [sopc2dts](https://github.com/LaurenceA4L/sopc2dts)
output) into a real HPS/kernel devicetree, resolving node/label/property
conflicts along the way.

## Overview

Board vendors ship a devicetree tree (Linux kernel + BSP) built from a fixed
reference FPGA design. When you replace that reference design with your own
Platform Designer / Qsys system, `sopc2dts` generates the qualified
fabric-side DTS — but it still needs to be grafted onto the HPS-side tree the
image actually boots with. `dts-merge` does that graft:

- Parses real-world DTS/DTSI source (via the C preprocessor, so `#include`s
  and dt-bindings macros like `GIC_SPI`/`IRQ_TYPE_*` resolve correctly) into
  the same node/property tree model `sopc2dts` already uses.
- Grafts an FPGA-side tree onto an anchor node/label in the HPS-side tree.
- Flags conflicts (duplicate node path or label, redefined properties,
  `&label` amendments with no matching target) instead of silently
  overwriting or dropping content, so a human — or the
  [fpga-embedded-studio](https://github.com/LaurenceA4L/fpga-embedded-studio)
  GUI — can resolve each one explicitly.

Intended as a companion to [sopc2dts](https://github.com/LaurenceA4L/sopc2dts)
and [cheby](https://gitlab.cern.ch/be-ics-hm/cheby) in a complete FPGA
register-map and devicetree workflow.

## Status

> **Early development.** API and CLI are not yet stable.

## Requirements

- Python 3.10+
- `cpp` (the C preprocessor — used to resolve `#include`s and macros in real
  kernel DTS/DTSI source; present on virtually any Linux toolchain)

## Installation

```bash
git clone https://github.com/LaurenceA4L/dts-merge.git
cd dts-merge
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows
.venv\Scripts\Activate.ps1

pip install -e .
```

## Usage

```
dts-merge --hps socfpga_agilex_socdk.dts --fpga my_system.dts \
    --anchor soc -o merged.dts
```

```
dts-merge --help
```

## Roadmap

- [x] Parse real DTS/DTSI (cpp + recursive-descent parser), including
      `&label { ... };` amendment blocks
- [x] Anchor-based graft of an FPGA-side tree onto an HPS-side tree
- [x] Conflict detection: duplicate path/label, property redefinition,
      orphaned `&label` amendments
- [x] CLI (`--auto base|fpga` for headless use)
- [ ] Overlay export scoped to user-level vs admin/calibration-level register
      subsets (planned; see fpga-embedded-studio)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

GPLv3 — see [COPYING](COPYING).

# dts-merge

A tool for merging Device Tree Source (DTS) fragments and overlays.

## Overview

`dts-merge` combines multiple DTS/DTSI fragments into a single coherent output, resolving node conflicts and property overrides. Intended as a companion to [sopc2dts](https://github.com/LaurenceA4L/sopc2dts) and [cheby](https://gitlab.cern.ch/be-ics-hm/cheby) in a complete FPGA register-map and devicetree workflow.

## Status

> **Early development.** API and CLI are not yet stable.

## Requirements

- Python 3.10+

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
dts-merge --help
```

## Roadmap

- [ ] Parse and merge DTS/DTSI fragments
- [ ] Resolve node path conflicts (last-writer-wins / merge strategies)
- [ ] Property override and delete support
- [ ] CLI and Python API
- [ ] Integration with sopc2dts output

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

GPLv3 — see [LICENCE](LICENCE).

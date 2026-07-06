# dts-merge - Interactive merge of a generated FPGA-fabric DTS into an HPS/kernel devicetree
#
# Copyright (C) 2026 Laurence <laurence@anodes4life.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""
End-to-end test against real fixtures pulled from an agilex_framework Yocto
checkout:

- ``fixtures/hps/socfpga_agilex7_socdk.dts``: not the vanilla kernel board
  file, but the *actual build artefact* — reconstructed by applying the exact
  ``cp``/``sed`` steps ``meta-intel-fpga-refdes``'s ``device-tree.bb``
  ``do_configure:append:agilex7_dk_si_agf014eb()`` runs (the MACHINE this
  project's own ``agilex_framework/.env`` builds), which injects
  ``#include "socfpga_agilex7_ghrd.dtsi"`` and ``#include "socfpga_ilc.dtsi"``
  right after ``#include "socfpga_agilex.dtsi"``. That means the generic GHRD
  peripherals (``led_pio``, ``button_pio``, ...) this tool exists to replace
  are genuinely present in this fixture, exactly as they are on a real build.
- ``fixtures/a7_fpga.dts``: real ``sopc2dts`` output for the ``a7_system``
  Platform Designer fixture (``sopc2dts/tests/fixtures/a7_system.sopcinfo``),
  generated with ``sopc2dts -i a7_system.sopcinfo -b boardinfo_a7.xml -t dts``.
"""

from pathlib import Path

from sopc2dts_py.model.devicetree import DTHelper
from dts_merge.merge import ConflictKind, Resolution, merge_trees
from dts_merge.parser import parse_dts, preprocess_and_parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_hps():
    return preprocess_and_parse(
        FIXTURES / "hps" / "socfpga_agilex7_socdk.dts",
        [FIXTURES / "hps" / "include"],
    )


def _load_fpga():
    return parse_dts((FIXTURES / "a7_fpga.dts").read_text())


def test_real_hps_chain_parses_with_expected_shape():
    hps = _load_hps()
    assert hps.unresolved_amendments == []
    soc = DTHelper.get_child_by_label(hps.root, "soc0")
    assert soc is not None
    # The real kernel /soc node has dozens of HPS peripherals, plus the
    # generic GHRD's led_pio/button_pio/dipsw_pio/trigger_pio/soc_leds/ilc —
    # a sanity floor, not an exact count that would make this test brittle
    # against future linux-socfpga/meta-intel-fpga-refdes updates.
    assert len(soc.children) > 40
    assert DTHelper.get_child_by_label(soc, "led_pio") is not None  # the generic GHRD one


def test_real_sopc2dts_output_parses():
    fpga = _load_fpga()
    sopc0 = DTHelper.get_child_by_label(fpga.root, "sopc0")
    assert sopc0 is not None
    assert DTHelper.get_child_by_label(fpga.root, "led_pio") is not None


def test_merging_real_qualified_output_surfaces_the_real_ghrd_collision():
    """
    This is the crux of the whole tool. Every real HPS peripheral must graft
    in without a fight (only the standard simple-bus properties both sides
    declare on their own container node collide: #address-cells, #size-cells,
    compatible, device_type, ranges) — but the actual build already carries
    the generic GHRD's own `led_pio` at a made-up address, and sopc2dts's
    qualified output has a *different*, real `led_pio` for this project's
    actual fabric. Same label, two different nodes — that collision must be
    surfaced, not silently resolved either way.
    """
    hps = _load_hps()
    fpga = _load_fpga()

    result = merge_trees(
        hps.root, fpga.root,
        base_anchor_label="soc0",
        fpga_anchor_label="sopc0",
        unresolved_fpga_amendments=fpga.unresolved_amendments,
    )

    kinds = {c.kind for c in result.conflicts}
    assert kinds == {ConflictKind.PROPERTY_REDEFINITION, ConflictKind.DUPLICATE_LABEL}

    prop_conflicts = [c for c in result.conflicts if c.kind == ConflictKind.PROPERTY_REDEFINITION]
    colliding_props = {c.fpga_prop.name for c in prop_conflicts}
    assert colliding_props == {"#address-cells", "#size-cells", "compatible", "device_type", "ranges"}

    label_conflicts = [c for c in result.conflicts if c.kind == ConflictKind.DUPLICATE_LABEL]
    assert len(label_conflicts) == 1
    dup = label_conflicts[0]
    assert dup.base_node.name == "gpio@f9001080"  # the generic GHRD led_pio
    assert dup.fpga_node.name == "gpio@1000"       # sopc2dts's own, real led_pio

    soc = DTHelper.get_child_by_label(result.merged_root, "soc0")
    assert DTHelper.get_child_by_label(soc, "hps_response_timer_0") is not None  # unrelated sopc2dts node, no fight


def test_resolving_all_conflicts_produces_serialisable_merged_tree():
    hps = _load_hps()
    fpga = _load_fpga()
    result = merge_trees(
        hps.root, fpga.root,
        base_anchor_label="soc0",
        fpga_anchor_label="sopc0",
        unresolved_fpga_amendments=fpga.unresolved_amendments,
    )
    for i in range(len(result.conflicts)):
        result.resolve(i, Resolution.FPGA)
    assert result.unresolved == []

    text = result.merged_root.to_string(0)
    assert "led_pio" in text
    assert "clkmgr" in text  # an unrelated real HPS peripheral, untouched

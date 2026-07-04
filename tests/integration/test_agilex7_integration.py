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

- ``fixtures/hps/socfpga_agilex_socdk.dts`` + ``socfpga_agilex.dtsi``: the
  real Linux kernel HPS-side devicetree for Agilex 7 (with the small subset
  of dt-bindings headers it needs to preprocess, under ``fixtures/hps/include``).
- ``fixtures/a7_fpga.dts``: real ``sopc2dts`` output for the ``a7_system``
  Platform Designer fixture (``sopc2dts/tests/fixtures/a7_system.sopcinfo``),
  generated with ``sopc2dts -i a7_system.sopcinfo -b boardinfo_a7.xml -t dts``.
- ``fixtures/socfpga_agilex7_ghrd.dtsi``: the generic GSRD reference-design
  fragment sopc2dts's output is meant to replace — kept here only so the test
  can assert the tool would *not* silently swallow the difference between
  "generic reference peripherals" and "this project's actual fabric".
"""

from pathlib import Path

from sopc2dts_py.model.devicetree import DTHelper
from dts_merge.merge import ConflictKind, Resolution, merge_trees
from dts_merge.parser import parse_dts, preprocess_and_parse

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_hps():
    return preprocess_and_parse(
        FIXTURES / "hps" / "socfpga_agilex_socdk.dts",
        [FIXTURES / "hps" / "include"],
    )


def _load_fpga():
    return parse_dts((FIXTURES / "a7_fpga.dts").read_text())


def test_real_hps_chain_parses_with_expected_shape():
    hps = _load_hps()
    assert hps.unresolved_amendments == []
    soc = DTHelper.get_child_by_label(hps.root, "soc0")
    assert soc is not None
    # The real kernel /soc node has dozens of HPS peripherals unrelated to
    # any FPGA fabric — a sanity floor, not an exact count that would make
    # this test brittle against future linux-socfpga updates.
    assert len(soc.children) > 20


def test_real_sopc2dts_output_parses():
    fpga = _load_fpga()
    sopc0 = DTHelper.get_child_by_label(fpga.root, "sopc0")
    assert sopc0 is not None
    assert DTHelper.get_child_by_label(fpga.root, "led_pio") is not None


def test_merging_real_qualified_output_only_conflicts_on_shared_bus_properties():
    """
    This is the crux of the whole tool: sopc2dts's qualified output should
    graft its actual fabric peripherals into the real HPS tree without
    colliding with any of the three dozen unrelated HPS peripherals. The only
    expected conflicts are the standard simple-bus properties both sides
    declare on their own container node (#address-cells, #size-cells,
    compatible, device_type, ranges) — never a duplicate peripheral path or
    label silently dropped.
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
    assert kinds == {ConflictKind.PROPERTY_REDEFINITION}
    colliding_props = {c.fpga_prop.name for c in result.conflicts}
    assert colliding_props == {"#address-cells", "#size-cells", "compatible", "device_type", "ranges"}

    soc = DTHelper.get_child_by_label(result.merged_root, "soc0")
    assert DTHelper.get_child_by_label(soc, "led_pio") is not None
    assert DTHelper.get_child_by_label(soc, "hps_response_timer_0") is not None


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


def test_generic_ghrd_peripherals_would_be_orphaned_if_merged_instead():
    """
    Guards against the actual motivating problem: the generic GHRD fragment
    (`led_pio`/`button_pio`/... at made-up addresses) sopc2dts's output is
    meant to replace must never be silently reconciled away — grafting it
    alongside sopc2dts's *own* `led_pio` (a real, different, per-project
    peripheral) has to surface as a conflict, not vanish.
    """
    hps = _load_hps()
    ghrd = parse_dts((FIXTURES / "socfpga_agilex7_ghrd.dtsi").read_text())

    result = merge_trees(
        hps.root, ghrd.root,
        base_anchor_label="soc0",
        unresolved_fpga_amendments=ghrd.unresolved_amendments,
    )
    # The generic fixture's own led_pio/button_pio/... don't collide with the
    # real HPS peripherals, so they graft in cleanly here...
    assert result.conflicts == []
    soc = DTHelper.get_child_by_label(result.merged_root, "soc0")
    assert DTHelper.get_child_by_label(soc, "led_pio") is not None

    # ...but merging the *qualified* sopc2dts output in on top of that would
    # immediately flag the label collision rather than silently pick one.
    fpga = _load_fpga()
    second = merge_trees(
        result.merged_root, fpga.root,
        base_anchor_label="soc0",
        fpga_anchor_label="sopc0",
        unresolved_fpga_amendments=fpga.unresolved_amendments,
    )
    dup_labels = {c.kind for c in second.conflicts}
    assert ConflictKind.DUPLICATE_LABEL in dup_labels

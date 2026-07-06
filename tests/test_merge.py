# dts-merge - Interactive merge of a generated FPGA-fabric DTS into an HPS/kernel devicetree
#
# Copyright (C) 2026 Laurence <laurence@anodes4life.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

import textwrap

import pytest

from sopc2dts_py.model.devicetree import DTHelper
from dts_merge.merge import ConflictKind, MergeError, Resolution, merge_trees
from dts_merge.parser import parse_dts


def _parse(text):
    return parse_dts(textwrap.dedent(text))


HPS = """
    / {
        compatible = "intel,socfpga-agilex";
        soc0: soc {
            #address-cells = <1>;
            #size-cells = <1>;
            compatible = "simple-bus";
            intc: intc@fffc1000 {
                compatible = "arm,gic-400";
            };
            gpio0: gpio@ff708000 {
                compatible = "snps,dw-apb-gpio";
            };
        };
    };
"""

FPGA = """
    / {
        sopc0: sopc@0 {
            #address-cells = <1>;
            #size-cells = <1>;
            compatible = "ALTR,avalon", "simple-bus";

            led_pio: gpio@10040 {
                compatible = "altr,pio-1.0";
            };

            gpio0: gpio@ff708000 {
                compatible = "duplicate,collision";
            };
        };
    };
    &intc {
        extra-prop = "from-fpga";
    };
    &missing_label {
        other-prop = "orphan";
    };
"""


def _merge():
    hps = _parse(HPS)
    fpga = _parse(FPGA)
    return merge_trees(
        hps.root, fpga.root,
        base_anchor_label="soc0",
        fpga_anchor_label="sopc0",
        unresolved_fpga_amendments=fpga.unresolved_amendments,
    )


def test_conflict_classes_detected():
    result = _merge()
    kinds = {c.kind for c in result.conflicts}
    assert kinds == {
        ConflictKind.PROPERTY_REDEFINITION,
        ConflictKind.DUPLICATE_PATH,
        ConflictKind.ORPHANED_AMENDMENT,
    }
    # #address-cells, #size-cells, compatible all collide on the anchor.
    assert sum(1 for c in result.conflicts if c.kind == ConflictKind.PROPERTY_REDEFINITION) == 3


def test_non_conflicting_child_is_grafted_immediately():
    result = _merge()
    soc = DTHelper.get_child_by_label(result.merged_root, "soc0")
    assert DTHelper.get_child_by_label(soc, "led_pio") is not None


def test_resolved_amendment_is_applied_to_base_label():
    result = _merge()
    intc = DTHelper.get_child_by_label(result.merged_root, "intc")
    assert intc.get_property_by_name("extra-prop").values[0].value == "from-fpga"


def test_resolve_base_keeps_existing_and_discards_fpga():
    result = _merge()
    dup = next(c for c in result.conflicts if c.kind == ConflictKind.DUPLICATE_PATH)
    idx = result.conflicts.index(dup)
    result.resolve(idx, Resolution.BASE)
    gpio0 = DTHelper.get_child_by_label(result.merged_root, "gpio0")
    assert gpio0.get_property_by_name("compatible").values[0].value == "snps,dw-apb-gpio"


def test_resolve_fpga_replaces_base_node():
    result = _merge()
    dup = next(c for c in result.conflicts if c.kind == ConflictKind.DUPLICATE_PATH)
    idx = result.conflicts.index(dup)
    result.resolve(idx, Resolution.FPGA)
    gpio0 = DTHelper.get_child_by_label(result.merged_root, "gpio0")
    assert gpio0.get_property_by_name("compatible").values[0].value == "duplicate,collision"


def test_resolve_both_keeps_both_nodes_with_distinct_labels():
    result = _merge()
    dup = next(c for c in result.conflicts if c.kind == ConflictKind.DUPLICATE_PATH)
    idx = result.conflicts.index(dup)
    result.resolve(idx, Resolution.BOTH)
    soc = DTHelper.get_child_by_label(result.merged_root, "soc0")
    names = sorted(c.name for c in soc.children)
    assert "gpio@ff708000" in names
    assert "gpio@ff708000_fpga" in names


def test_resolve_orphaned_amendment_base_discards_it():
    result = _merge()
    orphan = next(c for c in result.conflicts if c.kind == ConflictKind.ORPHANED_AMENDMENT)
    idx = result.conflicts.index(orphan)
    result.resolve(idx, Resolution.BASE)
    assert result.merged_root.get_property_by_name("other-prop") is None


def test_resolve_orphaned_amendment_fpga_attaches_at_root():
    result = _merge()
    orphan = next(c for c in result.conflicts if c.kind == ConflictKind.ORPHANED_AMENDMENT)
    idx = result.conflicts.index(orphan)
    result.resolve(idx, Resolution.FPGA)
    assert result.merged_root.get_property_by_name("other-prop").values[0].value == "orphan"


def test_unknown_anchor_label_raises():
    hps = _parse(HPS)
    fpga = _parse(FPGA)
    with pytest.raises(MergeError):
        merge_trees(hps.root, fpga.root, base_anchor_label="does-not-exist")


def test_unresolved_property_lists_before_resolution():
    result = _merge()
    assert len(result.unresolved) == len(result.conflicts)
    result.resolve(0, Resolution.BASE)
    assert len(result.unresolved) == len(result.conflicts) - 1


def test_duplicate_label_conflict_records_both_real_paths():
    """
    Same label, different unit addresses — the real led_pio scenario. Unlike
    DUPLICATE_PATH (where both sides are, by definition, at the same path),
    a DUPLICATE_LABEL conflict's two colliding nodes normally live at
    genuinely different addresses, so callers need both paths (and the
    shared label) to render something a human can act on.
    """
    hps = _parse("""
        / {
            soc0: soc {
                led_pio: gpio@f9001080 {
                    compatible = "altr,pio-1.0";
                };
            };
        };
    """)
    fpga = _parse("""
        / {
            sopc0: sopc@0 {
                led_pio: gpio@1000 {
                    compatible = "altr,pio-19.2.4";
                };
            };
        };
    """)
    result = merge_trees(
        hps.root, fpga.root,
        base_anchor_label="soc0",
        fpga_anchor_label="sopc0",
    )
    assert len(result.conflicts) == 1
    c = result.conflicts[0]
    assert c.kind == ConflictKind.DUPLICATE_LABEL
    assert c.label == "led_pio"
    assert c.base_path == "/soc/gpio@f9001080"
    assert c.fpga_path == "/soc/gpio@1000"

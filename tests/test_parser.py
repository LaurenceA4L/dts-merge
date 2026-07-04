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

from sopc2dts_py.model.devicetree import DTHelper, DTPropPHandleVal
from dts_merge.parser import DTSParseError, parse_dts


def test_basic_node_and_property_types():
    dts = textwrap.dedent("""
        / {
            model = "Test Board";
            #address-cells = <1>;
            #size-cells = <1>;

            node0: node@100 {
                compatible = "vendor,thing";
                reg = <0x100 0x10>;
                interrupts = <0 5 4>;
                gpio-controller;
                data = [ab cd ef];
            };
        };
    """)
    parsed = parse_dts(dts)
    root = parsed.root
    assert root.get_property_by_name("model").values[0].value == "Test Board"

    node = DTHelper.get_child_by_label(root, "node0")
    assert node is not None
    assert node.name == "node@100"
    reg = node.get_property_by_name("reg")
    assert [v.val for v in reg.values] == [0x100, 0x10]
    assert node.get_property_by_name("gpio-controller").values == []
    data = node.get_property_by_name("data")
    assert [v.val for v in data.values] == [0xAB, 0xCD, 0xEF]


def test_label_amendment_overrides_property_and_adds_children():
    dts = textwrap.dedent("""
        / {
            qspi: spi@ff8d2000 {
                status = "okay";
            };
        };
        &qspi {
            status = "disabled";
            extra-child {
                foo = "bar";
            };
        };
    """)
    parsed = parse_dts(dts)
    assert parsed.unresolved_amendments == []
    qspi = DTHelper.get_child_by_label(parsed.root, "qspi")
    assert qspi.get_property_by_name("status").values[0].value == "disabled"
    assert any(c.name == "extra-child" for c in qspi.children)


def test_amendment_for_missing_label_is_reported_unresolved():
    dts = "/ { }; &nonexistent { foo = \"bar\"; };"
    parsed = parse_dts(dts)
    assert len(parsed.unresolved_amendments) == 1
    label, fragment = parsed.unresolved_amendments[0]
    assert label == "nonexistent"
    assert fragment.get_property_by_name("foo").values[0].value == "bar"


def test_phandle_reference():
    dts = textwrap.dedent("""
        / {
            intc: intc@1 { };
            dev@2 {
                interrupt-parent = <&intc>;
            };
        };
    """)
    parsed = parse_dts(dts)
    dev = None
    for c in parsed.root.children:
        if c.name == "dev@2":
            dev = c
    assert dev is not None
    val = dev.get_property_by_name("interrupt-parent").values[0]
    assert isinstance(val, DTPropPHandleVal)
    assert val.label == "intc"


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("<1>", ["1"]),
        ("<0x10>", ["0x00000010"]),
        ("<(1 << 4)>", ["16"]),  # no hex-formatted operand -> decimal rendering
        ("<(0xff & 0x0f)>", ["0x0000000f"]),
        ("<(2 | 1)>", ["3"]),
        # A negative fold (e.g. `~0`) only round-trips through hex, which
        # DTPropHexNumVal wraps to unsigned 32-bit at render time.
        ("<~0>", ["0xffffffff"]),
    ],
)
def test_cell_expressions(expr, expected):
    dts = f"/ {{ node {{ val = {expr}; }}; }};"
    parsed = parse_dts(dts)
    node = parsed.root.children[0]
    vals = [v.value_str() for v in node.get_property_by_name("val").values]
    assert vals == expected


def test_multi_cell_unit_address_with_comma():
    dts = "/ { memory@1,80000000 { device_type = \"memory\"; }; };"
    parsed = parse_dts(dts)
    assert parsed.root.children[0].name == "memory@1,80000000"


def test_reopened_root_merges_into_single_tree():
    dts = textwrap.dedent("""
        / {
            model = "Base";
            soc { a { }; };
        };
        / {
            compatible = "vendor,board";
        };
    """)
    parsed = parse_dts(dts)
    assert parsed.root.get_property_by_name("model") is not None
    assert parsed.root.get_property_by_name("compatible") is not None
    assert any(c.name == "soc" for c in parsed.root.children)


def test_malformed_input_raises():
    with pytest.raises(DTSParseError):
        parse_dts("/ { node { foo = ; }; };")


def test_vendor_comma_property_names():
    dts = textwrap.dedent("""
        / {
            gpio@1 {
                altr,gpio-bank-width = <4>;
                altr,interrupt-type = <2>;
            };
        };
    """)
    parsed = parse_dts(dts)
    node = parsed.root.children[0]
    assert node.get_property_by_name("altr,gpio-bank-width").values[0].val == 4

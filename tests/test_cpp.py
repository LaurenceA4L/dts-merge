# dts-merge - Interactive merge of a generated FPGA-fabric DTS into an HPS/kernel devicetree
#
# Copyright (C) 2026 Laurence <laurence@anodes4life.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

from dts_merge.parser import parse_dts, preprocess_and_parse


def test_cpp_resolves_include_and_macros(tmp_path):
    header = tmp_path / "irq.h"
    header.write_text(
        "#define GIC_SPI 0\n"
        "#define IRQ_TYPE_LEVEL_HIGH 4\n"
        "#define IRQ_TYPE_EDGE_BOTH (2 | 1)\n"
    )
    board = tmp_path / "board.dts"
    board.write_text(
        '#include "irq.h"\n'
        "/ {\n"
        "    dev@1 {\n"
        "        interrupts = <GIC_SPI 5 IRQ_TYPE_LEVEL_HIGH>;\n"
        "        interrupts-both = <IRQ_TYPE_EDGE_BOTH>;\n"
        "    };\n"
        "};\n"
    )
    parsed = preprocess_and_parse(board)
    dev = parsed.root.children[0]
    interrupts = [v.val for v in dev.get_property_by_name("interrupts").values]
    assert interrupts == [0, 5, 4]
    both = [v.val for v in dev.get_property_by_name("interrupts-both").values]
    assert both == [3]


def test_cpp_resolves_chained_dtsi_include(tmp_path):
    base = tmp_path / "base.dtsi"
    base.write_text('/ { model = "Base"; soc { existing { }; }; };\n')
    board = tmp_path / "board.dts"
    board.write_text(
        '#include "base.dtsi"\n'
        '/ { compatible = "vendor,board"; };\n'
    )
    parsed = preprocess_and_parse(board)
    assert parsed.root.get_property_by_name("model") is not None
    assert parsed.root.get_property_by_name("compatible") is not None


def test_directives_are_stripped():
    parsed = parse_dts('/dts-v1/;\n/memreserve/ 0x1000 0x100;\n/ { };\n')
    assert parsed.root.name == "/"

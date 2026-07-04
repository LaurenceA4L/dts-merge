# dts-merge - Interactive merge of a generated FPGA-fabric DTS into an HPS/kernel devicetree
#
# Copyright (C) 2026 Laurence <laurence@anodes4life.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""
A small recursive-descent parser for real-world DTS/DTSI source text.

Unlike ``sopc2dts``'s generators (which only ever *build* a ``DTNode`` tree in
memory from a Platform Designer system), this module has to *read* DTS text
written by humans: kernel board files, BSP dtsi fragments, etc. That means
coping with C-preprocessor macros (``GIC_SPI``, ``IRQ_TYPE_LEVEL_HIGH``, ...),
``#include`` chains, and ``&label { ... };`` node-amendment blocks, none of
which sopc2dts ever needs to emit.

Rather than invent a second node/property tree, this parser builds
``sopc2dts_py.model.devicetree.DTNode``/``DTProperty`` trees directly, so the
merge engine (``dts_merge.merge``) only ever deals with one node model.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from sopc2dts_py.model.devicetree import (
    DTHelper,
    DTNode,
    DTProperty,
    DTPropBareRefVal,
    DTPropByteVal,
    DTPropHexNumVal,
    DTPropNumVal,
    DTPropPHandleVal,
    DTPropStringVal,
)


class DTSParseError(Exception):
    """Raised on malformed DTS/DTSI input."""


# ---------------------------------------------------------------------------
# cpp preprocessing
# ---------------------------------------------------------------------------

def run_cpp(path: Path, include_dirs: Optional[List[Path]] = None) -> str:
    """
    Run the C preprocessor over a DTS/DTSI file, resolving ``#include``s and
    dt-bindings macros (``GIC_SPI``, ``IRQ_TYPE_*``, ...) the same way the
    Linux kernel build does for its own devicetree sources.
    """
    if shutil.which("cpp") is None:
        raise DTSParseError("'cpp' was not found on PATH — install a C toolchain (gcc/clang).")

    path = Path(path)
    args = [
        "cpp",
        "-nostdinc",
        "-undef",
        "-D__DTS__",
        "-x", "assembler-with-cpp",
        "-I", str(path.parent),
    ]
    for d in include_dirs or []:
        args += ["-I", str(d)]
    args.append(str(path))

    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise DTSParseError(f"cpp failed on {path}:\n{result.stderr}")
    return result.stdout


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

_TOK_NUMBER = "NUMBER"
_TOK_IDENT = "IDENT"
_TOK_STRING = "STRING"
_TOK_REF = "REF"          # &label (no whitespace between & and the label)
_TOK_EOF = "EOF"

_SINGLE_CHAR_TOKS = "{}();,:=<>[]+-*/%|^~!@"

_NUMBER_RE = re.compile(r"0[xX][0-9a-fA-F]+|[0-9]+")
_IDENT_START_RE = re.compile(r"[#A-Za-z_]")
_IDENT_RE = re.compile(r"[#A-Za-z_][A-Za-z0-9,._+#-]*")
_LABEL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Token:
    kind: str
    text: str
    pos: int


def _strip_directives(text: str) -> str:
    """Drop /dts-v1/;, /plugin/; and /memreserve/ ...; — structurally irrelevant here."""
    text = re.sub(r"/dts-v1/\s*;", "", text)
    text = re.sub(r"/plugin/\s*;", "", text)
    text = re.sub(r"/memreserve/[^;]*;", "", text)
    return text


def tokenize(text: str) -> List[Token]:
    text = _strip_directives(text)
    toks: List[Token] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        # C-style comments (cpp normally strips these, but be defensive).
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        # cpp line markers, e.g. `# 1 "file.h" 1`
        if c == "#" and (i == 0 or text[i - 1] == "\n"):
            m = re.match(r"#\s*\d+\s+\"[^\"]*\"[^\n]*", text[i:])
            if m:
                i += m.end()
                continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    esc = text[j + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(esc, esc))
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            toks.append(Token(_TOK_STRING, "".join(buf), i))
            i = j + 1
            continue
        if c == "&":
            m = _LABEL_RE.match(text, i + 1)
            if m and (i + 1 == m.start()):
                toks.append(Token(_TOK_REF, m.group(0), i))
                i = m.end()
                continue
            toks.append(Token("&", "&", i))
            i += 1
            continue
        m = _NUMBER_RE.match(text, i)
        if m:
            j = m.end()
            # Skip integer literal suffixes (U/L/UL/LL, any case/order).
            while j < n and text[j] in "uUlL":
                j += 1
            toks.append(Token(_TOK_NUMBER, text[m.start():m.end()], i))
            i = j
            continue
        if _IDENT_START_RE.match(c):
            m = _IDENT_RE.match(text, i)
            toks.append(Token(_TOK_IDENT, m.group(0), i))
            i = m.end()
            continue
        if text.startswith("<<", i) or text.startswith(">>", i):
            toks.append(Token(text[i:i + 2], text[i:i + 2], i))
            i += 2
            continue
        if c in _SINGLE_CHAR_TOKS:
            toks.append(Token(c, c, i))
            i += 1
            continue
        raise DTSParseError(f"Unexpected character {c!r} at offset {i}")
    toks.append(Token(_TOK_EOF, "", n))
    return toks


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

@dataclass
class ParsedDTS:
    root: DTNode
    unresolved_amendments: List[Tuple[str, DTNode]] = field(default_factory=list)


_EXPR_OPS = {"|", "^", "&", "<<", ">>", "+", "-", "*", "/", "%"}


class _Parser:
    def __init__(self, tokens: List[Token]) -> None:
        self.toks = tokens
        self.i = 0

    # -- token helpers ----------------------------------------------------

    def peek(self, off: int = 0) -> Token:
        idx = self.i + off
        return self.toks[idx] if idx < len(self.toks) else self.toks[-1]

    def advance(self) -> Token:
        tok = self.toks[self.i]
        if self.i < len(self.toks) - 1:
            self.i += 1
        return tok

    def expect(self, kind: str) -> Token:
        tok = self.peek()
        if tok.kind != kind:
            raise DTSParseError(f"Expected {kind!r} but got {tok.kind!r} ({tok.text!r}) at offset {tok.pos}")
        return self.advance()

    # -- top level ----------------------------------------------------------

    def parse_document(self) -> ParsedDTS:
        root = DTNode("/")
        unresolved: List[Tuple[str, DTNode]] = []

        while self.peek().kind != _TOK_EOF:
            if self.peek().kind == "/" and self.peek(1).kind == "{":
                self.advance()  # '/'
                self._parse_node_body(root)
                self.expect(";")
                continue
            if self.peek().kind == _TOK_REF and self.peek(1).kind == "{":
                label = self.advance().text
                fragment = DTNode(f"&{label}")
                self._parse_node_body(fragment)
                self.expect(";")
                target = DTHelper.get_child_by_label(root, label)
                if target is not None:
                    self._merge_fragment_into(target, fragment)
                else:
                    unresolved.append((label, fragment))
                continue
            raise DTSParseError(
                f"Unexpected token {self.peek().kind!r} ({self.peek().text!r}) at top level, "
                f"offset {self.peek().pos}"
            )

        # Second pass: labels defined later in the same document.
        still_unresolved: List[Tuple[str, DTNode]] = []
        for label, fragment in unresolved:
            target = DTHelper.get_child_by_label(root, label)
            if target is not None:
                self._merge_fragment_into(target, fragment)
            else:
                still_unresolved.append((label, fragment))

        return ParsedDTS(root=root, unresolved_amendments=still_unresolved)

    @staticmethod
    def _merge_fragment_into(target: DTNode, fragment: DTNode) -> None:
        for prop in fragment.properties:
            target.add_property(prop, replace_existing=True)
        for child in fragment.children:
            target.add_child(child)

    # -- nodes ----------------------------------------------------------------

    def _parse_node_body(self, node: DTNode) -> None:
        self.expect("{")
        while self.peek().kind != "}":
            self._parse_statement(node)
        self.expect("}")

    def _parse_statement(self, parent: DTNode) -> None:
        label: Optional[str] = None
        if self.peek().kind == _TOK_IDENT and self.peek(1).kind == ":":
            label = self.advance().text
            self.advance()  # ':'

        name_tok = self.expect(_TOK_IDENT)
        name = name_tok.text

        if self.peek().kind == "@":
            self.advance()
            # Unit addresses are normally a single token ("f9001080", "0"), but
            # multi-cell addresses (e.g. "memory@1,80000000") tokenize as
            # several — concatenate raw text up to the node's opening brace.
            unit_parts = []
            while self.peek().kind not in ("{", _TOK_EOF):
                unit_parts.append(self.advance().text)
            if self.peek().kind == _TOK_EOF:
                raise DTSParseError(f"Unterminated node '{name}@...' before EOF")
            name = f"{name}@{''.join(unit_parts)}"

        nxt = self.peek().kind
        if nxt == "{":
            child = DTNode(name, label=label)
            self._parse_node_body(child)
            self.expect(";")
            parent.add_child(child)
        elif nxt == "=":
            self.advance()
            prop = DTProperty(name, label=label)
            self._parse_value_list(prop)
            self.expect(";")
            parent.add_property(prop, replace_existing=True)
        elif nxt == ";":
            self.advance()
            parent.add_property(DTProperty(name, label=label), replace_existing=True)
        else:
            tok = self.peek()
            raise DTSParseError(
                f"Expected '{{', '=' or ';' after '{name}' but got {tok.kind!r} at offset {tok.pos}"
            )

    # -- property values --------------------------------------------------

    def _parse_value_list(self, prop: DTProperty) -> None:
        while True:
            self._parse_value_segment(prop)
            if self.peek().kind == ",":
                self.advance()
                continue
            break

    def _parse_value_segment(self, prop: DTProperty) -> None:
        tok = self.peek()
        if tok.kind == _TOK_STRING:
            self.advance()
            prop.add_value(DTPropStringVal(tok.text))
        elif tok.kind == "<":
            self.advance()
            while self.peek().kind != ">":
                if self.peek().kind == _TOK_REF:
                    prop.add_value(DTPropPHandleVal(self.advance().text))
                else:
                    val, is_hex = self._parse_expr()
                    # DTS cells are unsigned; a negative fold (e.g. `~0`) only
                    # round-trips through the hex form, which wraps to 32-bit.
                    prop.add_value(
                        DTPropHexNumVal(val) if (is_hex or val < 0) else DTPropNumVal(val)
                    )
            self.expect(">")
        elif tok.kind == "[":
            self.advance()
            while self.peek().kind != "]":
                byte_tok = self.advance()
                prop.add_value(DTPropByteVal(int(byte_tok.text, 16)))
            self.expect("]")
        elif tok.kind == _TOK_REF:
            # Bare `foo = &label;` (no enclosing <>) — a dtc source-level
            # shorthand most often seen in `aliases`, expanded to a path
            # string by dtc itself at actual compile time.
            self.advance()
            prop.add_value(DTPropBareRefVal(tok.text))
        else:
            raise DTSParseError(f"Unexpected property value token {tok.kind!r} at offset {tok.pos}")

    # -- integer expressions (precedence-climbing, C-like) -----------------
    # or | xor ^ | and & | shift << >> | add + - | mul * / % | unary ~ - + | primary
    #
    # Each level returns (value, is_hex) — is_hex just tracks whether any
    # operand in the expression was written with a 0x prefix, so folded
    # results still round-trip as hex (registers/addresses/masks) rather than
    # every literal collapsing to an 8-digit hex constant.

    def _parse_expr(self) -> Tuple[int, bool]:
        return self._parse_bitor()

    def _parse_bitor(self) -> Tuple[int, bool]:
        v, hexy = self._parse_bitxor()
        while self.peek().kind == "|":
            self.advance()
            rv, rhexy = self._parse_bitxor()
            v |= rv
            hexy = hexy or rhexy
        return v, hexy

    def _parse_bitxor(self) -> Tuple[int, bool]:
        v, hexy = self._parse_bitand()
        while self.peek().kind == "^":
            self.advance()
            rv, rhexy = self._parse_bitand()
            v ^= rv
            hexy = hexy or rhexy
        return v, hexy

    def _parse_bitand(self) -> Tuple[int, bool]:
        v, hexy = self._parse_shift()
        while self.peek().kind == "&":
            self.advance()
            rv, rhexy = self._parse_shift()
            v &= rv
            hexy = hexy or rhexy
        return v, hexy

    def _parse_shift(self) -> Tuple[int, bool]:
        v, hexy = self._parse_add()
        while self.peek().kind in ("<<", ">>"):
            op = self.advance().kind
            rv, rhexy = self._parse_add()
            v = (v << rv) if op == "<<" else (v >> rv)
            hexy = hexy or rhexy
        return v, hexy

    def _parse_add(self) -> Tuple[int, bool]:
        v, hexy = self._parse_mul()
        while self.peek().kind in ("+", "-"):
            op = self.advance().kind
            rv, rhexy = self._parse_mul()
            v = v + rv if op == "+" else v - rv
            hexy = hexy or rhexy
        return v, hexy

    def _parse_mul(self) -> Tuple[int, bool]:
        v, hexy = self._parse_unary()
        while self.peek().kind in ("*", "/", "%"):
            op = self.advance().kind
            rv, rhexy = self._parse_unary()
            if op == "*":
                v *= rv
            elif op == "/":
                v = int(v / rv)
            else:
                v = v - int(v / rv) * rv
            hexy = hexy or rhexy
        return v, hexy

    def _parse_unary(self) -> Tuple[int, bool]:
        if self.peek().kind in ("~", "-", "+", "!"):
            op = self.advance().kind
            v, hexy = self._parse_unary()
            if op == "~":
                return ~v, hexy
            if op == "-":
                return -v, hexy
            if op == "!":
                return (0 if v else 1), hexy
            return v, hexy
        return self._parse_primary()

    def _parse_primary(self) -> Tuple[int, bool]:
        tok = self.peek()
        if tok.kind == "(":
            self.advance()
            v = self._parse_expr()
            self.expect(")")
            return v
        if tok.kind == _TOK_NUMBER:
            self.advance()
            return int(tok.text, 0), tok.text.lower().startswith("0x")
        raise DTSParseError(f"Expected a number or '(' but got {tok.kind!r} at offset {tok.pos}")


def parse_dts(text: str) -> ParsedDTS:
    """Parse already-preprocessed DTS text into a ``ParsedDTS``."""
    return _Parser(tokenize(text)).parse_document()


def preprocess_and_parse(path: Path, include_dirs: Optional[List[Path]] = None) -> ParsedDTS:
    """Run ``cpp`` over ``path`` and parse the result. The common entry point."""
    text = run_cpp(Path(path), include_dirs)
    return parse_dts(text)


__all__ = [
    "DTSParseError",
    "ParsedDTS",
    "Token",
    "tokenize",
    "parse_dts",
    "run_cpp",
    "preprocess_and_parse",
]

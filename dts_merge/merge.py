# dts-merge - Interactive merge of a generated FPGA-fabric DTS into an HPS/kernel devicetree
#
# Copyright (C) 2026 Laurence <laurence@anodes4life.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""
Graft a generated FPGA-fabric ``DTNode`` tree onto an HPS/kernel ``DTNode``
tree, surfacing conflicts instead of silently overwriting or dropping
content.

The "graft point" mirrors ``sopc2dts``'s own boardinfo overlay mechanism
(``BICDTAppend.parent_label`` / ``parent_path`` in
``sopc2dts_py/model/boardinfo.py``, applied by
``DTGenerator._do_dt_append``): look a node up by label (falling back to a
``/``-separated path), then attach to it. This module does the same lookup,
just for a whole subtree instead of one property/node at a time, and instead
of applying blindly it first checks whether attaching would collide with
something already there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

from sopc2dts_py.model.devicetree import DTHelper, DTNode, DTProperty


class MergeError(Exception):
    """Raised when the merge cannot proceed at all (e.g. anchor not found)."""


class ConflictKind(Enum):
    DUPLICATE_PATH = auto()
    DUPLICATE_LABEL = auto()
    PROPERTY_REDEFINITION = auto()
    ORPHANED_AMENDMENT = auto()


class Resolution(Enum):
    BASE = auto()   # keep the HPS/base side, discard the fpga side
    FPGA = auto()   # take the fpga side, discard/replace the base side
    BOTH = auto()   # keep both (fpga side is renamed to avoid re-colliding)
    EDIT = auto()   # caller supplied an explicit replacement


@dataclass
class Conflict:
    kind: ConflictKind
    path: str
    base_node: Optional[DTNode] = None
    fpga_node: Optional[DTNode] = None
    base_prop: Optional[DTProperty] = None
    fpga_prop: Optional[DTProperty] = None
    resolution: Optional[Resolution] = None
    replacement: Optional[object] = None  # DTNode or DTProperty, only set for EDIT
    # For DUPLICATE_LABEL specifically: the two colliding nodes normally live
    # at *different* paths (that's the whole problem) — ``path`` alone can't
    # show that, so these carry each side's real path for display.
    base_path: Optional[str] = None
    fpga_path: Optional[str] = None
    label: Optional[str] = None

    @property
    def resolved(self) -> bool:
        return self.resolution is not None


@dataclass
class MergeResult:
    merged_root: DTNode
    conflicts: List[Conflict] = field(default_factory=list)

    @property
    def unresolved(self) -> List[Conflict]:
        return [c for c in self.conflicts if not c.resolved]

    def resolve(
        self,
        index: int,
        resolution: Resolution,
        replacement: Optional[object] = None,
    ) -> None:
        """Apply a resolution to ``self.conflicts[index]``, mutating ``merged_root``."""
        conflict = self.conflicts[index]
        conflict.resolution = resolution
        conflict.replacement = replacement
        _apply_resolution(self.merged_root, conflict)


# ---------------------------------------------------------------------------
# Tree indexing
# ---------------------------------------------------------------------------

def _index_tree(node: DTNode, prefix: str = "") -> Tuple[Dict[str, DTNode], Dict[str, DTNode]]:
    """
    Return (path -> node, label -> node) for every node in the tree.
    ``prefix`` is a "/"-joined path with no trailing slash ("" for the root).
    """
    by_path: Dict[str, DTNode] = {}
    by_label: Dict[str, DTNode] = {}

    def walk(n: DTNode, path: str) -> None:
        by_path[path or "/"] = n
        if n.label:
            by_label[n.label] = n
        for child in n.children:
            walk(child, f"{path}/{child.name}")

    walk(node, prefix)
    return by_path, by_label


def _resolve_anchor(root: DTNode, label: Optional[str], path: Optional[List[str]]) -> DTNode:
    if label:
        node = DTHelper.get_child_by_label(root, label)
        if node is not None:
            return node
        raise MergeError(f"Anchor label '{label}' not found in base tree")
    if path:
        node = root
        for part in path:
            nxt = next((c for c in node.children if c.name.lower() == part.lower()), None)
            if nxt is None:
                raise MergeError(f"Anchor path '/{'/'.join(path)}' not found in base tree (at '{part}')")
            node = nxt
        return node
    return root


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_trees(
    base_root: DTNode,
    fpga_root: DTNode,
    base_anchor_label: Optional[str] = None,
    base_anchor_path: Optional[List[str]] = None,
    fpga_anchor_label: Optional[str] = None,
    unresolved_fpga_amendments: Optional[List[Tuple[str, DTNode]]] = None,
) -> MergeResult:
    """
    Graft ``fpga_root`` (or the node inside it named by ``fpga_anchor_label``,
    e.g. sopc2dts's own ``sopc0`` container) onto the node in ``base_root``
    identified by ``base_anchor_label``/``base_anchor_path`` (default: the
    base tree's own root).

    ``unresolved_fpga_amendments`` are ``&label {...}`` fragments the parser
    could not resolve within the fpga-side document alone (see
    ``dts_merge.parser.ParsedDTS.unresolved_amendments``) — common when the
    fpga output amends an HPS-side label (e.g. ``&intc``) that only exists in
    ``base_root``. Each is re-tried against the merged tree.
    """
    base_anchor = _resolve_anchor(base_root, base_anchor_label, base_anchor_path)
    fpga_anchor = fpga_root
    if fpga_anchor_label:
        found = DTHelper.get_child_by_label(fpga_root, fpga_anchor_label)
        if found is None:
            raise MergeError(f"fpga anchor label '{fpga_anchor_label}' not found in fpga tree")
        fpga_anchor = found

    conflicts: List[Conflict] = []
    by_path, by_label = _index_tree(base_root)
    anchor_path = next((p for p, n in by_path.items() if n is base_anchor), "/")

    # 1. The anchor node's own properties (e.g. sopc2dts's container sets
    #    #address-cells/#size-cells/compatible/ranges directly on itself,
    #    which very plausibly already exist on a real HPS `/soc` node).
    for fpga_prop in list(fpga_anchor.properties):
        base_prop = base_anchor.get_property_by_name(fpga_prop.name)
        if base_prop is None:
            base_anchor.add_property(fpga_prop)
        else:
            conflicts.append(Conflict(
                kind=ConflictKind.PROPERTY_REDEFINITION,
                path=anchor_path,
                base_node=base_anchor,
                fpga_node=fpga_anchor,
                base_prop=base_prop,
                fpga_prop=fpga_prop,
            ))

    # 2. Graft each of the fpga anchor's children.
    for fpga_child in list(fpga_anchor.children):
        child_path = f"{anchor_path.rstrip('/')}/{fpga_child.name}"
        dup_path_node = by_path.get(child_path)
        dup_label_node = by_label.get(fpga_child.label) if fpga_child.label else None

        if dup_path_node is not None:
            conflicts.append(Conflict(
                kind=ConflictKind.DUPLICATE_PATH,
                path=child_path,
                base_node=dup_path_node,
                fpga_node=fpga_child,
                base_path=child_path,
                fpga_path=child_path,
            ))
        elif dup_label_node is not None:
            base_node_path = next((p for p, n in by_path.items() if n is dup_label_node), child_path)
            conflicts.append(Conflict(
                kind=ConflictKind.DUPLICATE_LABEL,
                path=child_path,
                base_node=dup_label_node,
                fpga_node=fpga_child,
                base_path=base_node_path,
                fpga_path=child_path,
                label=fpga_child.label,
            ))
        else:
            base_anchor.add_child(fpga_child)
            new_paths, new_labels = _index_tree(fpga_child, child_path)
            by_path.update(new_paths)
            by_label.update(new_labels)

    # 3. Amendments the fpga-side parser couldn't resolve on its own — retry
    #    against the (now-merged) base tree.
    for label, fragment in unresolved_fpga_amendments or []:
        target = DTHelper.get_child_by_label(base_root, label)
        if target is not None:
            for prop in fragment.properties:
                target.add_property(prop, replace_existing=True)
            for child in fragment.children:
                target.add_child(child)
        else:
            conflicts.append(Conflict(
                kind=ConflictKind.ORPHANED_AMENDMENT,
                path=f"&{label}",
                fpga_node=fragment,
            ))

    return MergeResult(merged_root=base_root, conflicts=conflicts)


# ---------------------------------------------------------------------------
# Resolution application
# ---------------------------------------------------------------------------

def _apply_resolution(merged_root: DTNode, conflict: Conflict) -> None:
    if conflict.kind == ConflictKind.PROPERTY_REDEFINITION:
        _apply_property_resolution(conflict)
    elif conflict.kind in (ConflictKind.DUPLICATE_PATH, ConflictKind.DUPLICATE_LABEL):
        _apply_node_resolution(merged_root, conflict)
    elif conflict.kind == ConflictKind.ORPHANED_AMENDMENT:
        _apply_orphaned_resolution(merged_root, conflict)


def _apply_property_resolution(conflict: Conflict) -> None:
    node = conflict.base_node
    if conflict.resolution == Resolution.BASE:
        return  # already in place, nothing to do
    if conflict.resolution == Resolution.FPGA:
        node.add_property(conflict.fpga_prop, replace_existing=True)
    elif conflict.resolution == Resolution.EDIT:
        node.add_property(conflict.replacement, replace_existing=True)
    elif conflict.resolution == Resolution.BOTH:
        raise MergeError("'both' is not a valid resolution for a property conflict")


def _find_parent(root: DTNode, target: DTNode) -> Optional[DTNode]:
    for child in root.children:
        if child is target:
            return root
        found = _find_parent(child, target)
        if found is not None:
            return found
    return None


def _apply_node_resolution(merged_root: DTNode, conflict: Conflict) -> None:
    if conflict.resolution == Resolution.BASE:
        return  # discard the fpga side, base node already in place
    parent = _find_parent(merged_root, conflict.base_node)
    if parent is None:
        raise MergeError(f"Could not locate parent of conflicting node '{conflict.path}' to resolve it")
    if conflict.resolution == Resolution.FPGA:
        parent.replace_child(conflict.base_node, conflict.fpga_node)
    elif conflict.resolution == Resolution.BOTH:
        conflict.fpga_node.name = f"{conflict.fpga_node.name}_fpga"
        if conflict.fpga_node.label:
            conflict.fpga_node.label = f"{conflict.fpga_node.label}_fpga"
        parent.add_child(conflict.fpga_node)
    elif conflict.resolution == Resolution.EDIT:
        parent.replace_child(conflict.base_node, conflict.replacement)


def _apply_orphaned_resolution(merged_root: DTNode, conflict: Conflict) -> None:
    if conflict.resolution == Resolution.BASE:
        return  # discard — the amendment target genuinely doesn't exist
    if conflict.resolution == Resolution.FPGA:
        # Best-effort: attach the orphaned fragment's contents directly under
        # the tree root rather than lose them silently.
        for prop in conflict.fpga_node.properties:
            merged_root.add_property(prop, replace_existing=True)
        for child in conflict.fpga_node.children:
            merged_root.add_child(child)
    elif conflict.resolution == Resolution.EDIT:
        for prop in conflict.replacement.properties:
            merged_root.add_property(prop, replace_existing=True)
        for child in conflict.replacement.children:
            merged_root.add_child(child)

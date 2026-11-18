"""LEGO element numbers (the "6058242" under each part in a booklet's parts list) -> part and colour.

From Rebrickable's public downloads (rebrickable.com/downloads, shipped in bicheck/data):
elements.csv (element_id, part_num, color_id, design_id) and colors.csv (id, name, rgb,
is_trans). Rebrickable's colour numbers mostly are LDraw's, but not all (its 179 is Flat Silver,
LDraw's 179 Pearl Silver), so colours are matched by name, then by the nearest RGB.
"""
from __future__ import annotations

import csv
import gzip
import os
import re
from functools import lru_cache

from .mapping import Mapping

HERE = os.path.join(os.path.dirname(__file__), "data")


def _norm(name: str) -> str:
    s = name.lower().replace("gray", "grey").replace("trans-", "trans ")
    s = re.sub(r"\blt\b", "light", s)
    s = re.sub(r"\bdk\b", "dark", s)
    s = re.sub(r"\bmed\b", "medium", s)
    return re.sub(r"[^a-z0-9]", "", s)


@lru_cache(maxsize=1)
def _table(path: str = "") -> dict:
    out = {}
    with gzip.open(path or os.path.join(HERE, "rebrickable_elements.csv.gz"), "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["element_id"]] = (r["part_num"], int(r["color_id"]), r.get("design_id") or "")
    return out


@lru_cache(maxsize=1)
def _colors() -> dict:
    with gzip.open(os.path.join(HERE, "rebrickable_colors.csv.gz"), "rt", encoding="utf-8") as f:
        return {int(r["id"]): (r["name"], r["rgb"], r["is_trans"] == "True") for r in csv.DictReader(f)}


class Elements:
    def __init__(self, m: Mapping):
        self.m = m
        self.table = _table()
        self.colors = _colors()
        self._ldraw: dict[int, int | str] = {}
        names = {_norm(n): c for c, n in m.color_names.items() if isinstance(c, int) and c < 10000}
        rgbs = {c: v for c, v in m.color_rgb.items() if isinstance(c, int) and c < 10000}
        for rid, (name, rgb, trans) in self.colors.items():
            c = names.get(_norm(name))
            if c is None and rid in m.color_names and rgb:
                c = rid if _close(rgbs.get(rid, ""), rgb) else None
            if c is None and rgb and rgbs:
                cand = [k for k in rgbs if ("trans" in m.color_names.get(k, "").lower()) == trans] or list(rgbs)
                c = min(cand, key=lambda k: _dist(rgbs[k], rgb))
            self._ldraw[rid] = c if c is not None else rid

    def color(self, rebrickable_id: int):
        return self._ldraw.get(rebrickable_id, rebrickable_id)

    def color_name(self, rebrickable_id: int) -> str:
        return self.colors.get(rebrickable_id, (f"Colour {rebrickable_id}",))[0]

    def get(self, element: str, prefer: set = frozenset()):
        """(design, LDraw colour) or None. Of LEGO's design number, Rebrickable's part number and
        that cut to its base (a print or an assembly: 973c26h01pr0391 -> 973): the one the model
        uses (`prefer`), else one the tables know, else the first there is."""
        row = self.table.get(element)
        if row is None:
            return None
        part, color, design = row
        base = re.sub(r"(c\d+h\d+|pr\d+|pat\d+|px\d+).*$", "", part)
        names = [d for d in (design, part, base) if d]
        known = lambda d: d in self.m.parts or d in self.m.rebrickable
        pick = next((d for d in names if d in prefer), None) or next((d for d in names if known(d)), names[0])
        return pick, self.color(color)


def _hex(s: str):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4)) if len(s) == 6 else (128, 128, 128)


def _dist(a: str, b: str) -> float:
    return sum((x - y) ** 2 for x, y in zip(_hex(a), _hex(b)))


def _close(a: str, b: str) -> bool:
    return bool(a) and _dist(a, b) < 40 ** 2

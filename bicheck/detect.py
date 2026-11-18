"""Which set a model is of, read from the model file: its name and its header.

Looks for set numbers in the file name, in the main model's header ("0 Downton Abbey",
"0 Name: 21373.mpd", "0 !KEYWORDS LEGO 21373", comments before the first part) and in its
submodels' names, and for set names ("Downton Abbey") in the same places. Set names come from
data/sets.csv.gz, Rebrickable's list of sets (packaging/make_sets.py makes it).
"""
from __future__ import annotations

import csv
import gzip
import os
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from . import ldraw


@dataclass
class Guess:
    sku: str
    name: str
    why: str        # "the file name", "the model's title", ...
    available: bool = True   # in our list of sets

    def __str__(self) -> str:
        return f"{self.sku} {self.name}".strip()


@lru_cache(maxsize=1)
def _table() -> list[tuple[str, str, int]]:
    """(set number, name, parts) of every numbered LEGO set (data/sets.csv.gz, from Rebrickable)."""
    try:
        with gzip.open(os.path.join(os.path.dirname(__file__), "data", "sets.csv.gz"), "rt", encoding="utf-8") as f:
            return [(r["set"], r["name"], int(r["parts"] or 0)) for r in csv.DictReader(f)]
    except (OSError, ValueError, KeyError):
        return []


@lru_cache(maxsize=1)
def sets() -> dict[str, str]:
    """Set number -> name."""
    return {sku: name for sku, name, _ in _table()}


@lru_cache(maxsize=1)
def _named() -> dict[str, str]:
    """The sets a name in a model can point to: names of two words or more that only one set has
    ("Downton Abbey"; not "Fire Station", which many sets are called, or "Castle"), of sets with
    100 parts or more."""
    count = Counter(name.lower() for _, name, _ in _table())
    return {sku: name for sku, name, parts in _table()
            if parts >= 100 and count[name.lower()] == 1 and len(_norm(name).split()) >= 2}


def _norm(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", s.lower()).strip() + " "


def _sources(path: str) -> list[tuple[str, str]]:
    """(text, where it came from), most telling first."""
    out = [(os.path.splitext(os.path.basename(path))[0], "the file name"),
           (os.path.basename(os.path.dirname(os.path.abspath(path))), "the folder name"),
           (os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(path)))), "the folder above")]
    try:
        text = ldraw.read_io(path) if path.lower().endswith(".io") else open(path, encoding="utf-8-sig", errors="replace").read()
    except (OSError, ValueError):
        return out
    header, subs = [], []
    in_main = True
    for line in text.splitlines()[:20000]:
        line = line.strip()
        if line.upper().startswith("0 FILE "):
            if header:
                in_main = False
            if ".dat" not in line.lower():  # custom parts ("71819.dat Copy 2") name parts, not sets
                subs.append(line[7:])
            continue
        if in_main and line.startswith("0 ") and not line.upper().startswith(("0 STEP", "0 ROTSTEP", "0 !LDCAD", "0 BFC")):
            header.append(line[2:])
        elif line.startswith("1 "):
            in_main = False
    out.append((" | ".join(header[:40]), "the model's header"))
    out.append((" | ".join(subs[:60]), "its submodel names"))
    return out


def _plausible(num: str, why: str, text: str) -> bool:
    """A number our list doesn't know is taken as a set number only from the file name, or
    from a folder name where it stands alone as a word ("HAUNTED HOUSE - 10273", not inside
    "4d55-9190-82e0", and not a year: "Lego 2024"); numbers in headers may be anything (part counts, dates)."""
    if why == "the file name":
        return True
    if why not in ("the folder name", "the folder above"):
        return False
    if 1950 <= int(num) <= 2035:
        return False
    return bool(re.search(rf"(?:^|[\s_(#]){num}(?:-\d{{1,2}})?(?:$|[\s_)])", text))


_DATE = re.compile(r"(19|20)\d\d-\d\d-\d\d")


def guess(path: str) -> list[Guess]:
    """Sets the model names, best first: set numbers in the order of where they were found (file
    name, folder, header, submodels; ones our list doesn't know only from file
    and folder names, where a number is most likely the set's), then set names."""
    known = sets()
    found: dict[str, Guess] = {}
    srcs = _sources(path)
    numbers = []
    for text, why in srcs:  # set numbers: 21373, 21373-1
        text = _DATE.sub(" ", text)  # 6662_Backup_2023-01-17: not set 2023
        for num in re.findall(r"(?<![\d.])(\d{4,7})(?:-\d{1,2})?(?!\d|\.\d)", text):
            numbers.append((num, why))
        # a short set number starting the file name ("850_fork_lift_truck", "852-1"), if it is one
        m3 = re.match(r"(\d{3})(?:-\d{1,2})?(?=[\s_-]|$)", text) if why == "the file name" else None
        if m3 and m3[1] in known:
            numbers.append((m3[1], why))
    rank = {why: i for i, (_, why) in enumerate(srcs)}
    for num, why in sorted(numbers, key=lambda nw: (rank[nw[1]], nw[0] not in known)):
        if num in found:
            continue
        if num in known:
            found[num] = Guess(num, known[num], why)
        elif len(num) <= 6 and _plausible(num, why, srcs[rank[why]][0]):
            found[num] = Guess(num, "", why, available=False)
    for text, why in srcs:  # set names, longest first so "Downton Abbey" beats "Abbey"
        t = _norm(text)
        for sku, name in sorted(_named().items(), key=lambda kv: -len(kv[1])):
            if len(name) >= 6 and sku not in found and _norm(name) in t:
                found[sku] = Guess(sku, name, why)
    return list(found.values())  # numbers by where they were found, then names


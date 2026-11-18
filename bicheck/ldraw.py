"""An LDraw model (.mpd / .ldr, or a BrickLink Studio .io) as a flat list of placed parts, with where each one is written.

Submodels are the MPD's "0 FILE" sections and .ldr/.mpd files beside the model. A section
that says it is a part ("0 !LDRAW_ORG ... Part", an LDCad-generated hose or spring, a
tex2ldraw print embedded in the MPD) counts as one part, like any library file.

Each part keeps its place in the file: the chain of submodels down to it, and in the last
one the step ("0 STEP" lines, counted from 1) and the line number. `global_step` numbers the
steps the way LDCad and LPub show them: a submodel's steps come before the step that first
uses it, and a submodel used again is not counted again.
"""
from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Placed:
    file: str             # the part's file name as written ("3023.dat")
    color: int | str
    M: np.ndarray         # world 3x3 matrix
    p: np.ndarray         # world position, LDU
    where: tuple          # ((submodel, step, line), ...) from the main model down
    global_step: int = 0

    @property
    def submodel(self) -> str:
        return self.where[-1][0]

    @property
    def step(self) -> int:
        return self.where[-1][1]

    @property
    def line(self) -> int:
        return self.where[-1][2]


@dataclass
class Sub:
    name: str
    lines: list = field(default_factory=list)   # (line number, text)
    is_part: bool = False


@dataclass
class Model:
    path: str
    main: str
    parts: list           # [Placed]
    subs: dict            # name (lower) -> Sub
    steps: dict = field(default_factory=dict)   # (submodel lower, step) -> global step


STEP_RE = re.compile(r"0\s+(STEP|ROTSTEP)\b", re.I)
_PART_TYPES = re.compile(r"^0\s+!LDRAW_ORG\s+(Unofficial_)?(Part|Subpart|Primitive|8_Primitive|48_Primitive|Shortcut)",
                         re.I)


def _split(text: str, name: str) -> list[Sub]:
    subs: list[Sub] = []
    cur = None
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("﻿")
        if line.upper().startswith("0 FILE "):
            cur = Sub(line[7:].strip())
            subs.append(cur)
            continue
        if line.upper().startswith("0 NOFILE"):
            cur = None
            continue
        if cur is None:
            if subs:  # text between files: ignore
                continue
            cur = Sub(name)
            subs.append(cur)
        cur.lines.append((i, line))
    for s in subs:
        head = [t for _, t in s.lines[:30]]
        s.is_part = any(_PART_TYPES.match(t) for t in head) or any(t.upper().startswith("0 !LDCAD GENERATED") for t in head)
    return subs


STUDIO_PASSWORD = b"soho0909"  # the password BrickLink Studio zips its .io files with


def read_io(path: str) -> str:
    """The LDraw model inside a BrickLink Studio .io file.

    An .io is a zip (encrypted with Studio's fixed password since Studio 2.x) holding
    model.ldr: an MPD with LDraw part names and LDraw colour codes. (model2.ldr beside it
    uses Studio's own colour codes and embeds the parts, so it is not the one to read.)"""
    with zipfile.ZipFile(path) as z:
        names = {n.lower(): n for n in z.namelist()}
        name = names.get("model.ldr") or names.get("modelv1.ldr")
        if name is None:
            raise ValueError(f"{os.path.basename(path)} has no model.ldr: not a BrickLink Studio model?")
        try:
            data = z.read(name)
        except RuntimeError:  # encrypted
            data = z.read(name, pwd=STUDIO_PASSWORD)
    return data.decode("utf-8-sig", errors="replace")


def read(path: str) -> Model:
    if path.lower().endswith(".io") or zipfile.is_zipfile(path):
        text = read_io(path)
        folder = ""  # everything is inside the .io
    else:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
        folder = os.path.dirname(os.path.abspath(path))
    subs: dict[str, Sub] = {}
    order = _split(text, os.path.basename(path))
    for s in order:
        subs.setdefault(s.name.lower().replace("\\", "/"), s)
    main = order[0].name.lower() if order else ""

    def sub(name: str) -> Sub | None:
        key = name.lower().replace("\\", "/")
        if key in subs:
            return subs[key]
        if folder and key.endswith((".ldr", ".mpd")):  # a submodel in its own file beside the model
            p = os.path.join(folder, *name.replace("\\", "/").split("/"))
            if os.path.isfile(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    for s in _split(f.read(), name):
                        subs.setdefault(s.name.lower().replace("\\", "/"), s)
                return subs.get(key)
        return None

    parts: list[Placed] = []
    steps: dict[tuple, int] = {}
    counter = [0]
    done: set[str] = set()

    def number_steps(name: str) -> None:
        """LPub-style global step numbers: a submodel's steps before the step that first uses it."""
        s = sub(name)
        key = name.lower()
        if s is None or s.is_part or key in done:
            return
        done.add(key)
        step, content = 1, False
        for _, line in s.lines:
            if line.startswith("1 "):
                f = line.split(None, 14)
                if len(f) == 15:
                    number_steps(f[14])
                    content = True
            elif STEP_RE.match(line):
                counter[0] += 1
                steps[(key, step)] = counter[0]
                step, content = step + 1, False
        if content:
            counter[0] += 1
            steps[(key, step)] = counter[0]

    def walk(name: str, color, M: np.ndarray, p: np.ndarray, where: tuple, depth: int) -> None:
        s = sub(name)
        if s is None or depth > 64:
            return
        step = 1
        for ln, line in s.lines:
            if STEP_RE.match(line):
                step += 1
                continue
            if not line.startswith("1 "):
                continue
            f = line.split(None, 14)
            if len(f) < 15:
                continue
            try:
                c = int(f[1]) if f[1].isdigit() else f[1]
                v = [float(x) for x in f[2:14]]
            except ValueError:
                continue
            c = color if c == 16 else c
            local_M = np.array(v[3:12]).reshape(3, 3)
            wM, wp = M @ local_M, p + M @ np.array(v[:3])
            here = where + ((s.name, step, ln),)
            child = sub(f[14])
            if child is not None and not child.is_part:
                walk(f[14], c, wM, wp, here, depth + 1)
            else:
                parts.append(Placed(f[14].strip(), c, wM, wp, here))

    if order:
        number_steps(order[0].name)
        walk(order[0].name, 16, np.eye(3), np.zeros(3), (), 0)
    for pt in parts:
        pt.global_step = steps.get((pt.submodel.lower(), pt.step), 0)
    return Model(path, main, parts, subs, steps)

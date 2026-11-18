"""LEGO design and colour ids <-> LDraw parts and colours.

Read from what is installed on this computer (nothing is shipped):

- BrickLink Studio's ldraw_lxfml_mapping.json and ldraw.xml, LDD's ldraw.xml: which LDraw
  file a LEGO design is, how it sits in the LDD mould's frame, printed parts by texture.
- LDConfig.ldr from the LDraw library: colour names, and LEGO colour ids from its
  "// LEGOID 194 - Light Bluish Gray" comments. Part descriptions come from the library too.

An LDraw file stands for a set of LEGO designs (the tables', Rebrickable's, its own number):
LDraw 60601 is LEGO 7179, 3070b is 3070. The comparison picks the one the instructions use,
so the model and the instructions agree whichever LDraw variant the builder picked. The placement math is tex2ldraw's (set_model).
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

LDU = 25.0                       # LDraw units per LDD unit
S = np.diag([1.0, -1.0, -1.0])   # LDraw axes <-> LDD axes

STUDIO_DATA = [r"C:\Program Files\Studio 2.0\data", r"C:\Program Files (x86)\Studio 2.0\data",
               "/Applications/Studio 2.0/data", "/Applications/Studio 2.0/Studio 2.0.app/Contents/Resources/data"]
LDD_XML = [r"C:\Program Files (x86)\LEGO Company\LEGO Digital Designer\ldraw.xml",
           r"C:\Program Files\LEGO Company\LEGO Digital Designer\ldraw.xml",
           "/Applications/LEGO Digital Designer.app/Contents/Resources/ldraw.xml"]


@dataclass(frozen=True)
class Entry:
    file: str                                       # "3001.dat"
    R: tuple = (1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0)    # column-vector form
    t: tuple = (0.0, 0.0, 0.0)                      # LDD units

    @property
    def rot(self) -> np.ndarray:
        return np.array(self.R, float).reshape(3, 3)


def axis_angle(axis, angle: float) -> np.ndarray:
    a = np.asarray(axis, float)
    n = np.linalg.norm(a)
    if n < 1e-12 or abs(angle) < 1e-12:
        return np.eye(3)
    x, y, z = a / n
    c, s, C = np.cos(angle), np.sin(angle), 1 - np.cos(angle)
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


def euler_xyz(x: float, y: float, z: float) -> np.ndarray:
    """Studio's rotation in degrees: about Z, then X, then Y (R = Ry Rx Rz)."""
    rx, ry, rz = (axis_angle(ax, np.radians(v)) for ax, v in (((1, 0, 0), x), ((0, 1, 0), y), ((0, 0, 1), z)))
    return ry @ rx @ rz


def placement(e: Entry, R: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(3x3 LDraw matrix, position in LDU) of LDraw part e on an LDD bone (R, t)."""
    A = S @ e.rot @ R @ S
    b = LDU * (t - np.asarray(e.t) @ e.rot @ R) @ S
    return A.T, b


def stem(file: str) -> str:
    f = file.strip().lower().replace("\\", "/").rsplit("/", 1)[-1]
    return f[:-4] if f.endswith((".dat", ".ldr")) else f


# -- the LDraw library ---------------------------------------------------------------
class Library:
    """Reads files from an LDraw folder or complete.zip; missing library = empty answers."""

    def __init__(self, path: str = ""):
        self.path = path
        self.zip = None
        if path and path.lower().endswith(".zip") and os.path.isfile(path):
            self.zip = zipfile.ZipFile(path)
            self.names = {n.lower().removeprefix("ldraw/"): n for n in self.zip.namelist()}

    def read(self, rel: str) -> str | None:
        rel = rel.replace("\\", "/")
        if self.zip is not None:
            n = self.names.get(rel.lower())
            return self.zip.read(n).decode("utf-8", "replace") if n else None
        if not self.path:
            return None
        for cand in (rel, rel.lower()):
            p = os.path.join(self.path, *cand.split("/"))
            if os.path.isfile(p):
                with open(p, encoding="utf-8", errors="replace") as f:
                    return f.read()
        return None

    @lru_cache(maxsize=None)
    def description(self, file: str, hops: int = 0) -> str:
        name = stem(file) + ".dat"
        for d in ("parts/", "unofficial/parts/", "parts/s/"):
            text = self.read(d + name)
            if text:
                first = text.lstrip("\ufeff").splitlines()[0].strip() if text.strip() else ""
                desc = first[1:].strip() if first.startswith("0") else ""
                moved = re.match(r"~Moved to\s+(\S+)", desc)
                if moved and hops < 3:  # 3023 -> 3023b: the new part's name
                    return self.description(moved.group(1), hops + 1) or desc
                return desc
        return ""


def find_library() -> str:
    env = os.environ.get("LDRAWDIR")
    if env and os.path.exists(env):
        return env
    home = os.path.expanduser("~")
    try:  # tex2ldraw's setting, when it is installed
        with open(os.path.join(home, ".config", "tex2ldraw", "settings.json"), encoding="utf-8") as f:
            p = json.load(f).get("ldraw_dir") or ""
        if p and os.path.exists(p):
            return p
    except (OSError, ValueError):
        pass
    for p in (os.path.join(home, "Documents", "LDraw"), os.path.join(home, "ldraw"), os.path.join(home, "LDraw"),
              r"C:\LDraw", r"C:\Program Files\LDraw", r"C:\Users\Public\Documents\LDraw", "/usr/share/ldraw"):
        if os.path.isfile(os.path.join(p, "LDConfig.ldr")):
            return p
    for p in (os.path.join(home, "Documents"), os.path.join(home, "Downloads"), home):
        if os.path.isfile(os.path.join(p, "complete.zip")):
            return os.path.join(p, "complete.zip")
    return ""


_COLOUR_RE = re.compile(r"^0\s+!COLOUR\s+(\S+)\s+CODE\s+(\d+)\s+VALUE\s+#([0-9A-Fa-f]{6})")
_LEGOID_RE = re.compile(r"^0\s+//\s*LEGOID\s+([\d\s/]+?)\s+-")


# -- the mapping -----------------------------------------------------------------------
@dataclass
class Mapping:
    parts: dict = field(default_factory=dict)        # design -> [Entry]
    prints: dict = field(default_factory=dict)       # (design, deco id) -> [Entry]
    print_files: dict = field(default_factory=dict)  # LDraw stem -> (design, deco id)
    colors: dict = field(default_factory=dict)       # LEGO colour id -> LDraw code
    color_names: dict = field(default_factory=dict)  # LDraw code -> name
    color_rgb: dict = field(default_factory=dict)    # LDraw code -> "#rrggbb"
    rebrickable: dict = field(default_factory=dict)  # design -> [LDraw stem]
    sources: list = field(default_factory=list)
    lib: Library = field(default_factory=Library)
    inverse: dict = field(default_factory=dict)      # LDraw stem -> {design}

    def finish(self) -> None:
        for design, entries in self.parts.items():
            for e in entries:
                self.inverse.setdefault(stem(e.file), set()).add(design)
        for design, files in self.rebrickable.items():
            for f in files:
                self.inverse.setdefault(stem(f), set()).add(design)

    def designs_of_file(self, file: str) -> set[str]:
        """LEGO designs an LDraw file may stand for: the tables', Rebrickable's, and its own number
        (3070b -> 3070, 3626c -> 3626)."""
        s = stem(file)
        out = set(self.inverse.get(s, ()))
        out.add(s)
        m = re.fullmatch(r"(\d+)[a-z]?\d?", s)
        if m:
            out.add(m.group(1))
            out |= self.inverse.get(m.group(1), set())
        return out

    def key_of_file(self, file: str) -> tuple[set[str], str]:
        """(designs it may be, print): print is "" plain, "?" a print we can't name, else the decoration id."""
        s = stem(file)
        if s in self.print_files:
            design, deco = self.print_files[s]
            return {design} | self.designs_of_file(s), deco
        m = re.fullmatch(r"(.+?)p(\d{6,8})", s)  # tex2ldraw's made prints: <base>p<decoration>.dat
        if m:
            return self.designs_of_file(m.group(1)), m.group(2)
        m = re.fullmatch(r"(\d+[a-z]?)(?:p[a-z0-9]{2,6}|pr\d{4}|d[a-z0-9]{2,4})", s)  # LDraw prints, stickers: 3068bpb0123, 3068bd2z
        if m:
            return self.designs_of_file(s) | self.designs_of_file(m.group(1)), "?"
        return self.designs_of_file(s), ""

    def color(self, material: str) -> int | str:
        if material.startswith("ldraw:"):   # already an LDraw code (from the booklets' element table)
            return int(material[6:])
        c = self.colors.get(material)
        return c if c is not None else f"LEGO {material}"

    def color_name(self, code) -> str:
        if isinstance(code, str):
            return code
        return self.color_names.get(code, f"Colour {code}").replace("_", " ")


def read_studio_json(path: str, m: Mapping) -> None:
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    for r in rows:
        ldd, ld = r.get("ldd") or {}, r.get("ldraw") or {}
        design, file = str(ldd.get("designId", "")), (ld.get("filename") or "").strip()
        if not design or not file:
            continue
        rot, tr = r.get("rotation") or {}, r.get("translation") or {}
        R = euler_xyz(rot.get("x", 0), rot.get("y", 0), rot.get("z", 0))
        scale = 1 / LDU if (tr.get("unit") or "LDU") == "LDU" else 1.0
        e = Entry(file, tuple(R.ravel()), tuple(tr.get(k, 0) * scale for k in "xyz"))
        kind = r.get("type")
        if kind == "decoration" and ldd.get("decoId"):
            from .lxfml import deco_id
            d = deco_id(ldd["decoId"])
            m.prints.setdefault((design, d), []).append(e)
            m.print_files.setdefault(stem(file), (design, d))
        elif kind in ("transformation", "flexible", "assembly"):
            m.parts.setdefault(design, []).append(e)
    m.sources.append(path)


def read_ldraw_xml(path: str, m: Mapping) -> None:
    text = open(path, encoding="utf-8", errors="replace").read()
    attrs = lambda s: dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', s))
    trans: dict[str, dict] = {}
    for tag, body in re.findall(r"<(Material|Brick|Transformation)\b([^>]*)/?>", text):
        a = attrs(body)
        if tag == "Material" and a.get("lego") and a.get("ldraw", "").lstrip("-").isdigit():
            m.colors.setdefault(a["lego"], int(a["ldraw"]))
        elif tag == "Transformation" and a.get("ldraw") and not a.get("type"):
            trans.setdefault(a["ldraw"].lower(), a)

    def entry(file, a):
        if a is None:
            return Entry(file)
        R = axis_angle([float(a.get(k, 0)) for k in ("ax", "ay", "az")], float(a.get("angle", 0)))
        return Entry(file, tuple(R.ravel()), tuple(float(a.get(k, 0)) for k in ("tx", "ty", "tz")))
    named = set()
    for body in re.findall(r"<Brick\b([^>]*)/?>", text):
        a = attrs(body)
        if a.get("lego") and a.get("ldraw"):
            m.parts.setdefault(a["lego"], []).append(entry(a["ldraw"], trans.get(a["ldraw"].lower())))
            named.add(a["lego"])
    for file, a in trans.items():
        if a.get("lego") and a["lego"] not in named:
            m.parts.setdefault(a["lego"], []).append(entry(a["ldraw"], a))
    m.sources.append(path)


def read_ldconfig(text: str, m: Mapping) -> None:
    ids: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        g = _LEGOID_RE.match(line)
        if g:
            ids = [i.strip() for i in g.group(1).split("/") if i.strip()]
            continue
        c = _COLOUR_RE.match(line)
        if c:
            code = int(c.group(2))
            m.color_names[code] = c.group(1)
            m.color_rgb[code] = "#" + c.group(3).lower()
            for i in ids:
                m.colors[i] = code  # LDConfig's own LEGOIDs win over the tables'
        ids = []


def read_rebrickable(m: Mapping) -> None:
    """Rebrickable's LEGO design -> LDraw part numbers: the snapshot shipped here (tex2ldraw's), and
    tex2ldraw's live lookups when it has made some. These join designs the tables don't know
    (LEGO 7179 is LDraw 60601) and tables' files the library lacks (Studio's bl_8136 is 60849)."""
    def _number(design: str) -> str:
        return str(int(design[2:])) if re.fullmatch(r"11\d{6}", design) else design
    try:
        with open(os.path.join(os.path.dirname(__file__), "data", "rebrickable_ldraw.json"), encoding="utf-8") as f:
            b = json.load(f)
        lego, ld = b.get("lego") or {}, b.get("ldraw") or {}
        for design, parts in lego.items():
            for rb in parts:
                for f in ld.get(rb, [rb]):
                    m.rebrickable.setdefault(design, []).append(f)
        for rb, files in ld.items():
            if rb not in lego and re.fullmatch(r"\d+", rb):
                m.rebrickable.setdefault(rb, []).extend(files)
        m.sources.append("Rebrickable snapshot")
    except (OSError, ValueError):
        pass
    live = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
                        "tex2ldraw", "rebrickable", "parts.json")
    try:
        with open(live, encoding="utf-8") as f:
            for design, e in json.load(f).items():
                if isinstance(e, dict) and e.get("ldraw"):
                    m.rebrickable.setdefault(_number(design), []).extend(e["ldraw"])
        m.sources.append(live)
    except (OSError, ValueError):
        pass


def default_tables() -> list[str]:
    found = []
    for d in STUDIO_DATA:
        for name in ("ldraw_lxfml_mapping.json", "ldraw.xml"):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                found.append(p)
    return found + [p for p in LDD_XML if os.path.isfile(p)]


def load(tables: list[str] | None = None, library: str | None = None) -> Mapping:
    m = Mapping()
    for p in tables if tables else default_tables():
        (read_studio_json if p.lower().endswith(".json") else read_ldraw_xml)(p, m)
    read_rebrickable(m)
    m.lib = Library(find_library() if library is None else library)
    cfg = m.lib.read("LDConfig.ldr")
    if cfg:
        read_ldconfig(cfg, m)
    m.finish()
    return m

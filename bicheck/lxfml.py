"""Building instructions in LXFML: every part, and the step that adds it.

What the file holds (the same format tex2ldraw's set_model reads):

- <Bricks>: every element with its final place. A <Brick> has one <Part> per mould (a
  minifig's legs are hips + two legs); a Part has designID ("3001;C"), materials ("21:0",
  LEGO colour ids), maybe a decoration ("1083800_0_VME_...;A:surface,...") and a <Bone
  transformation="a,...,l">: rotation rows and translation in LDD units, p' = p @ R + t.
- <BuildingInstructions>: one <BuildingInstruction> per book, <Steps> of <Step>s. A step
  adds bricks (<In brickRef=uuid>), finished sub-builds (<SubBuild>, itself a list of
  steps) and copies of a sub-build built several times (<MultiBuild>, bricks by uuid or
  only by name). <VisibilityRange>s that hide bricks to the end of a book mark spares and
  alternates (a second head), which are not in the finished model.
- <Bags>: <NumberedBag name="..."> lists the bricks in each bag.

Some sets carry an extra "overview" book (one step with the whole model). Each brick takes
its step from the book with the most steps that brings it in.

Step numbers: steps are counted in reading order, a sub-build's steps before the step that
adds the finished sub-build, counting every step that adds something. `path` is the same
step as top-level step + sub-build steps ("68.5": step 5 of the sub-build in top step 68).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np


@dataclass
class BIPart:
    brick: str            # brick uuid (a minifig's parts share it)
    design: str           # LEGO design id ("3023")
    material: str         # LEGO colour id ("194")
    deco: str             # decoration brief id ("1083800"), "" when plain
    R: np.ndarray         # bone rotation, row-vector form: p' = p @ R + t (LDD units)
    t: np.ndarray
    sticker: bool = False
    step: int = 0         # index into BI.steps; 0 = in no step
    spare: bool = False   # hidden at the end of its book: not in the finished model
    bag: str = ""


@dataclass
class Step:
    number: int           # 1-based, in reading order
    path: str             # "68.5"
    book: str
    bricks: list = field(default_factory=list)   # brick uuids added in this step
    subbuilds: int = 0                           # finished sub-builds added in this step
    label: str = ""                              # the number printed in a booklet, when not `number`


@dataclass
class BI:
    parts: list           # [BIPart]
    steps: list           # [Step], steps[0] is a dummy for "no step"
    books: list           # book names in reading order
    title: str = ""


def _bone(s: str) -> tuple[np.ndarray, np.ndarray]:
    n = [float(x) for x in s.split(",")]
    return np.array(n[:9]).reshape(3, 3), np.array(n[9:12])


def deco_id(decoration: str) -> str:
    """'1083800_0_VME_...;A:surface,...' -> '1083800' (the decoration brief its textures are named after)."""
    items = sorted(i.strip().partition(":")[0].partition(";")[0] for i in (decoration or "").split(",") if i.strip())
    return items[0].partition("_")[0] if items else ""


def _read_bricks(root: ET.Element) -> tuple[dict, list[BIPart]]:
    bricks, parts = {}, []
    holder = root.find("Bricks")
    for b in holder if holder is not None else []:
        if b.tag != "Brick" or not b.get("designID"):
            continue
        key = b.get("uuid") or b.get("refID") or str(len(bricks))
        bricks[key] = b.get("designID", "").partition(";")[0]
        for p in b.iter("Part"):
            bone = next((x.get("transformation") for x in p.findall("Bone") if x.get("transformation")), None)
            if not bone:
                continue
            R, t = _bone(bone)
            mats = [x.split(":")[0] for x in (p.get("materials") or "").split(",") if x]
            material = next((x for x in mats if x != "0"), mats[0] if mats else "")
            parts.append(BIPart(key, p.get("designID", "").partition(";")[0], material,
                                deco_id(p.get("decoration") or ""), R, t, p.get("partType") == "sticker"))
    return bricks, parts


def _book_order(root: ET.Element, books: list[ET.Element]) -> list[ET.Element]:
    """Books in the order of the BIGraph's dependencies (document order where it says nothing)."""
    by_id = {b.get("uuid"): b for b in books}
    node_bi = {n.get("uuid"): n.get("buildingInstructionRef") for n in root.iter("BINode")}
    before: dict = {}
    for d in root.iter("Dependency"):
        a, b = node_bi.get(d.get("predecessorRef")), node_bi.get(d.get("successorRef"))
        if a in by_id and b in by_id:
            before.setdefault(b, set()).add(a)
    done, order = set(), []

    def visit(u, seen=()):
        if u in done or u in seen:
            return
        for p in sorted(before.get(u, ()), key=lambda x: books.index(by_id[x])):
            visit(p, (*seen, u))
        done.add(u)
        order.append(by_id[u])
    for b in books:
        visit(b.get("uuid"))
    return order


def _hidden_at_end(book: ET.Element) -> set[str]:
    out = set()
    for vr in book.iter("VisibilityRange"):
        if vr.get("visibilityState", "hidden") == "hidden" and not vr.get("endStepRef"):
            out |= {b.get("brickRef") for b in vr.iter("Brick") if b.get("brickRef")}
    return out


class _Walker:
    """Numbers one book's steps and hands each brick to the first step that adds it."""

    def __init__(self, book: ET.Element, name: str, steps: list, bricks: dict, owner: dict, skip: set,
                 take_copies: bool):
        self.name, self.steps, self.bricks, self.owner, self.skip = name, steps, bricks, owner, skip
        ins = {i.get("brickRef") for i in book.iter("In")}
        # MultiBuild copies named only by design: free bricks of that design no <In> brings in
        self.free: dict[str, list[str]] = {}
        if take_copies:
            for u, design in bricks.items():
                if u not in ins and u not in owner and u not in skip:
                    self.free.setdefault(design, []).append(u)

    def copy(self, name: str) -> str | None:
        free = self.free.get(name.partition(" - ")[0].strip(), [])
        return free.pop(0) if free else None

    def walk(self, container: ET.Element, path: str) -> None:
        i = 0
        for st in container:
            if st.tag != "Step":
                continue
            i += 1
            here = f"{path}{i}"
            if any(c.tag == "Step" for c in st):
                self.walk(st, here + "/")
            subs = 0
            for c in st:
                if c.tag == "SubBuild":
                    n = len(self.steps)
                    self.walk(c, here + ".")
                    subs += len(self.steps) > n
            added = []
            for c in st:
                if c.tag == "In":
                    added.append(c.get("brickRef", ""))
                elif c.tag == "MultiBuild":
                    for mb in c.iter("MultiBuildBrick"):
                        u = mb.get("brickRef") or self.copy(mb.get("name") or "")
                        if u:
                            added.append(u)
            added = [u for u in dict.fromkeys(added) if u in self.bricks and u not in self.owner and u not in self.skip]
            if not added and not subs:
                continue
            step = Step(len(self.steps), here, self.name, added, subs)
            for u in added:
                self.owner[u] = step.number
            self.steps.append(step)


def read(text: str) -> BI:
    root = ET.fromstring(text)
    # Older files point at bricks by refID ("540"), newer ones by uuid: make them all uuids.
    ids = {b.get("refID"): b.get("uuid") for b in root.iter("Brick") if b.get("refID") and b.get("uuid")}
    for e in root.iter():
        if e.get("brickRef") in ids:
            e.set("brickRef", ids[e.get("brickRef")])
    bricks, parts = _read_bricks(root)
    books = _book_order(root, [b for b in root.iter("BuildingInstruction") if b.find("Steps") is not None])
    # The book with the most steps decides a brick's step; overview books only fill in the rest.
    rank = sorted(books, key=lambda b: -sum(1 for _ in b.iter("Step")))
    owner: dict[str, int] = {}
    spares: set[str] = set()
    per_book: dict[int, list[Step]] = {}
    for b in rank:
        spares |= (_hidden_at_end(b) & set(bricks)) - set(owner)
        steps: list[Step] = [Step(0, "", "")]
        w = _Walker(b, b.get("name") or "", steps, bricks, owner, spares, take_copies=True)
        w.walk(b.find("Steps"), "")
        per_book[id(b)] = steps[1:]
    # Number the steps across books in reading order, leaving out books that add nothing.
    all_steps = [Step(0, "", "")]
    renum: dict[tuple, int] = {}
    names = []
    for b in books:
        st = per_book.get(id(b), [])
        if not st:
            continue
        names.append(b.get("name") or f"Book {len(names) + 1}")
        for s in st:
            new = len(all_steps)
            renum[(id(b), s.number)] = new
            all_steps.append(Step(new, s.path, names[-1], s.bricks, s.subbuilds))
    # owner holds per-book numbers: map them through the book that set them
    book_of: dict[str, int] = {}
    for b in rank:
        for s in per_book.get(id(b), []):
            for u in s.bricks:
                book_of.setdefault(u, renum[(id(b), s.number)])
    bags = {}
    for bag in root.iter("NumberedBag"):
        for x in bag.iter("Brick"):
            if x.get("brickRef"):
                bags[x.get("brickRef")] = bag.get("name") or ""
    for p in parts:
        p.step = book_of.get(p.brick, 0)
        p.spare = p.brick in spares
        p.bag = bags.get(p.brick, "")
    if not books:  # a plain model file without instructions: everything in one step
        all_steps.append(Step(1, "1", "", list(bricks)))
        for p in parts:
            p.step = 1
    return BI(parts, all_steps, names)


def load(path: str) -> BI:
    with open(path, encoding="utf-8", errors="replace") as f:
        return read(f.read())

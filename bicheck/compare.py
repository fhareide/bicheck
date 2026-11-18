"""Compare an LDraw model with its building instructions.

Two answers, the second only when the model can be lined up with the instructions:

1. Counts, like LDCad's part list check: for each part and colour, how many the instructions
   use and how many the model has, and every step that uses it.
2. Part by part. Every brick in the instructions has its final place, and so does every
   part in the model. The model is turned and moved onto the instructions (it may sit
   anywhere and face any way: the right-angled turn and the shift that put the most
   identical parts on top of each other win). Pieces moved as a whole (a floor lifted off
   to show the inside) are found the same way, one after another. Then each model part is
   paired with the instructions' part in the same spot, and a pair that differs is the
   mistake, with its step: "step 742 wants Dark Bluish Gray here, the model has Light
   Bluish Gray". What is left over is missing (in the instructions, not in the model) or
   extra (in the model, not in the instructions).
"""
from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from . import ldraw, lxfml, progress
from .mapping import Entry, Mapping, placement, stem

TOL = 3.0        # LDU: the same part in the same spot (the tables' placements are good to about a quarter)
TOL_SWAP = 6.0   # LDU: a different part in that spot (another mould may have its origin a little elsewhere)
NEAR = 40.0      # LDU: "near step N" hints for left-over parts
STEP_REACH = 40.0  # LDU (2 studs): how far the same part may sit and still be "this one, moved", within linked steps
MIN_PIECE = 8
SAME_COLORS = [(179, 315), (315, 179)]  # (in the model, in the instructions): one colour, two LDraw codes    # parts that must line up for a separately moved piece to count


def rotations() -> list[np.ndarray]:
    out = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            R = np.zeros((3, 3))
            for i, (j, s) in enumerate(zip(perm, signs)):
                R[i, j] = s
            if np.linalg.det(R) > 0:
                out.append(R)
    out.sort(key=lambda R: -np.trace(R))  # unturned first
    return out


@dataclass
class Item:
    side: str            # "bi" | "model"
    index: int           # into Result.bi or Result.model
    family: str          # LEGO design ("3023"); "f:<file>" for a model part the instructions don't have
    deco: str            # "" plain, "?" a print we can't name, else the decoration id
    color: int | str
    pos: np.ndarray
    file: str            # LDraw file (the model's, or the one the tables give)
    families: frozenset = frozenset()   # every design in the instructions the file may be

    @property
    def key(self):
        return (self.family, self.deco, self.color)


@dataclass
class Issue:
    kind: str            # colour | part | part+colour | print | missing | extra | offset | swap
    bi: int | None       # index into Result.bi_items
    model: int | None    # index into Result.model_items
    step: int            # instructions step (0 = none)
    near: int | None = None   # extras: a step whose parts are close by; swaps: the step the part belongs to
    distance: float = 0.0
    fig: bool = False    # known to be part of a minifigure (set by a matcher that knows more)


@dataclass
class Piece:
    R: np.ndarray
    t: np.ndarray
    matched: int


@dataclass
class CountRow:
    family: str
    color: int | str
    bi: int
    model: int
    prints_bi: Counter = field(default_factory=Counter)       # decoration -> qty ("" plain)
    prints_model: Counter = field(default_factory=Counter)
    bi_steps: Counter = field(default_factory=Counter)        # step -> qty
    model_items: list = field(default_factory=list)          # model item indices


@dataclass
class Result:
    bi: lxfml.BI
    model: ldraw.Model
    mapping: Mapping
    bi_items: list
    model_items: list
    issues: list
    counts: list
    pieces: list
    pairs: dict                    # model item -> bi item, every pair (good ones too)
    skipped: Counter               # what was left out of the comparison, and why
    names: dict                    # family -> display file

    @property
    def lined_up(self) -> float:
        return len(self.pairs) / max(1, min(len(self.bi_items), len(self.model_items)))


# -- building the two lists -------------------------------------------------------------
def _entry_for(m: Mapping, p: lxfml.BIPart, used: dict) -> Entry:
    cands = []
    if p.deco:
        cands = m.prints.get((p.design, p.deco), [])
    cands = cands or m.parts.get(p.design, [])
    if not cands:
        return Entry(p.design + ".dat")
    fam_use = used.get(p.design, Counter())
    return max(cands, key=lambda e: fam_use.get(stem(e.file), 0))


def _assembly(m: Mapping, file: str) -> list[tuple[str, object, np.ndarray, np.ndarray]]:
    """The parts an LDraw assembly is made of ("3829c01.dat": steering wheel and holder), as
    (file, colour, matrix, position) in the assembly's frame; [] for a single part."""
    text = m.lib.read("parts/" + file)
    if not text:
        return []
    out = []
    for line in text.splitlines():
        f = line.split()
        if len(f) < 15 or f[0] != "1":
            continue
        name = " ".join(f[14:]).replace("\\", "/")
        if "/" in name or not m.lib.read("parts/" + name):
            continue  # sub-parts (s/...) and primitives are pieces of one part, not parts
        v = [float(x) for x in f[2:14]]
        c = int(f[1]) if f[1].lstrip("-").isdigit() else f[1]
        out.append((name, c, np.array(v[3:]).reshape(3, 3), np.array(v[:3])))
    return out if len(out) >= 2 else []


def build_items(bi: lxfml.BI, model: ldraw.Model, m: Mapping) -> tuple[list[Item], list[Item], Counter, dict]:
    skipped = Counter()
    designs = Counter(p.design for p in bi.parts if not p.sticker and not p.spare)
    used: dict[str, Counter] = defaultdict(Counter)   # design -> LDraw stems the model uses for it
    model_items: list[Item] = []
    for i, p in enumerate(model.parts):
        if m.lib.description(p.file).lower().startswith("sticker"):
            skipped["sticker in the model"] += 1
            continue
        cands, deco = m.key_of_file(p.file)
        fams = frozenset(d for d in cands if d in designs)
        if not fams:  # an assembly the instructions build from its parts: compare those
            subs = _assembly(m, p.file)
            if any(d in designs for f, *_ in subs for d in m.key_of_file(f)[0]):
                for f, c, M, q in subs:
                    sc, sd = m.key_of_file(f)
                    sf = frozenset(d for d in sc if d in designs) or frozenset({"f:" + stem(f)})
                    for d in sf:
                        used[d][stem(f)] += 1
                    model_items.append(Item("model", i, max(sf, key=lambda d: (designs.get(d, 0), d)), sd,
                                            p.color if c == 16 else c, p.p + p.M @ q, f, sf))
                continue
            fams = frozenset({"f:" + stem(p.file)})
        family = max(fams, key=lambda d: (designs.get(d, 0), d))
        for d in fams:
            used[d][stem(p.file)] += 1
        model_items.append(Item("model", i, family, deco, p.color, p.p, p.file, fams))
    bi_items: list[Item] = []
    for i, p in enumerate(bi.parts):
        if p.sticker:
            skipped["sticker in the instructions"] += 1
            continue
        if p.spare:
            skipped["spare or alternate in the instructions"] += 1
            continue
        if not p.step:
            skipped["in no instructions step"] += 1
            continue
        e = _entry_for(m, p, used)
        _, pos = placement(e, p.R, p.t)
        bi_items.append(Item("bi", i, p.design, p.deco, m.color(p.material), pos, e.file, frozenset({p.design})))
    # LDraw has two near-identical flat silvers: 179 (Pearl Silver, the old code many models
    # use) and 315 (Flat Silver, LEGO's own number). Same colour, when the instructions use only one.
    bi_colors = {it.color for it in bi_items}
    for a, b in SAME_COLORS:
        if b in bi_colors and a not in bi_colors:
            for it in model_items:
                if it.color == a:
                    it.color = b
                    skipped[f"{m.color_name(a)} read as {m.color_name(b)}"] += 1
    names: dict[str, str] = {}
    for it in bi_items:  # the model's spelling when it has the part, else the tables'
        u = used.get(it.family)
        names.setdefault(it.family, (u.most_common(1)[0][0] + ".dat") if u else it.file)
    for it in model_items:
        names.setdefault(it.family, it.file)
    return bi_items, model_items, skipped, names


# -- lining up ----------------------------------------------------------------------------
class Grid:
    def __init__(self, pos: np.ndarray, idx: list[int], cell: float):
        self.cell, self.pos = cell, pos
        self.cells: dict[tuple, list[int]] = defaultdict(list)
        for i in idx:
            self.cells[tuple(np.floor(pos[i] / cell).astype(int))].append(i)

    def near(self, q: np.ndarray, r: float):
        c = np.floor(q / self.cell).astype(int)
        n = int(np.ceil(r / self.cell))
        for dx in range(-n, n + 1):
            for dy in range(-n, n + 1):
                for dz in range(-n, n + 1):
                    for i in self.cells.get((c[0] + dx, c[1] + dy, c[2] + dz), ()):
                        d = float(np.linalg.norm(self.pos[i] - q))
                        if d <= r:
                            yield i, d


def _by_key(items: list[Item], idx) -> dict:
    out = defaultdict(list)
    for i in idx:
        out[items[i].key].append(i)
    return out


def _score(bpos, bkeys, mpos, mkeys, R, t) -> int:
    n = 0
    for k, mi in mkeys.items():
        bi = bkeys.get(k)
        if not bi:
            continue
        q = mpos[mi] @ R.T + t
        d = np.linalg.norm(q[:, None, :] - bpos[bi][None, :, :], axis=2)
        n += int(min((d.min(axis=1) < TOL).sum(), len(bi)))
    return n


def yaw(Ms) -> float:
    """How far parts are turned about the vertical off right angles (a car parked at an angle),
    in radians within +-45 degrees: the most common turn of their sideways axes."""
    a = [np.arctan2(M[2, 0], M[0, 0]) for M in Ms if abs(M[1, 0]) < 0.1]
    if len(a) < 3:
        return 0.0
    a = np.round(np.degrees(np.mod(np.array(a) + np.pi / 4, np.pi / 2) - np.pi / 4))
    v, n = np.unique(a, return_counts=True)
    return float(np.radians(v[np.argmax(n)])) if n.max() >= 0.5 * len(a) else 0.0


def turn_y(angle: float) -> np.ndarray:
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def register(b_items, m_items, b_free, m_free, min_piece: int = MIN_PIECE, share=None,
             turn: np.ndarray | None = None) -> Piece | None:
    """The right-angled turn and shift that put the most identical model parts on the instructions'
    (after `turn`, when the parts sit at an angle of their own)."""
    if not b_free or not m_free:
        return None
    bpos = np.array([it.pos for it in b_items])
    mpos = np.array([it.pos for it in m_items])
    bkeys, mkeys = _by_key(b_items, b_free), _by_key(m_items, m_free)
    shared = [k for k in mkeys if k in bkeys]
    # rare parts vote first: few pairs, each pair one candidate shift
    shared.sort(key=lambda k: len(bkeys[k]) * len(mkeys[k]))
    budget, keys = 0, []
    for k in shared:
        n = len(bkeys[k]) * len(mkeys[k])
        if budget + n > 60000 and keys:
            break
        budget += n
        keys.append(k)
    best = None
    rots = rotations() if turn is None else [R @ turn for R in rotations()]
    for ri, R in enumerate(rots):
        if share:
            progress.tick(share[0] + (share[1] - share[0]) * ri / len(rots), f"trying turn {ri + 1} of {len(rots)}")
        votes = []
        for k in keys:
            q = mpos[mkeys[k]] @ R.T
            votes.append((bpos[bkeys[k]][None, :, :] - q[:, None, :]).reshape(-1, 3))
        if not votes:
            continue
        v = np.round(np.concatenate(votes) / 2.0).astype(np.int64)
        uniq, cnt = np.unique(v, axis=0, return_counts=True)
        # a big piece: the shifts most pairs agree on; a small one (a lifted submodel): try them all
        top = np.argsort(-cnt)[:3] if len(m_free) > 40 else np.argsort(-cnt)[:400]
        for j in top:
            if cnt[j] < (3 if len(m_free) > 40 else 1):
                break
            t = uniq[j] * 2.0
            s = _score(bpos, bkeys, mpos, mkeys, R, t)
            if best is None or s > best[0]:
                best = (s, R, t)
    if best is None or best[0] < min_piece:
        return None
    s, R, t = best
    # refine the shift: the median offset of the parts that line up
    res = []
    for k, mi in mkeys.items():
        bi = bkeys.get(k)
        if not bi:
            continue
        q = mpos[mi] @ R.T + t
        d = q[:, None, :] - bpos[bi][None, :, :]
        n = np.linalg.norm(d, axis=2)
        for a, b in zip(*np.nonzero(n < TOL)):
            res.append(d[a, b])
    if res:
        t = t - np.median(np.array(res), axis=0)
    return Piece(R, t, s)


def pair_up(b_items, m_items, b_free: set, m_free: set, piece: Piece) -> list[tuple[int, int, float]]:
    """Pairs (bi, model, distance) in the piece's frame: identical parts first, then the rest."""
    bpos = np.array([it.pos for it in b_items])
    grid = Grid(bpos, sorted(b_free), 8.0)
    cand = []
    for mi in m_free:
        q = m_items[mi].pos @ piece.R.T + piece.t
        for bi, d in grid.near(q, TOL_SWAP):
            a, b = b_items[bi], m_items[mi]
            same = a.family in b.families
            if same and a.color == b.color and a.deco == b.deco:
                rank = 0
            elif same and a.color == b.color:
                rank = 1   # the print differs
            elif same or a.color == b.color:
                rank = 2
            elif d < 1.0:
                rank = 3
            else:
                continue
            if rank == 0 and d > TOL:
                continue
            cand.append((rank, d, bi, mi))
    cand.sort()
    out = []
    for rank, d, bi, mi in cand:
        if bi in b_free and mi in m_free:
            b_free.discard(bi)
            m_free.discard(mi)
            out.append((bi, mi, d))
    return out


# -- the comparison --------------------------------------------------------------------------
def compare(bi: lxfml.BI, model: ldraw.Model, m: Mapping, max_pieces: int = 8) -> Result:
    progress.stage("lineup")
    b_items, m_items, skipped, names = build_items(bi, model, m)
    step_of = lambda i: bi.parts[b_items[i].index].step
    b_free, m_free = set(range(len(b_items))), set(range(len(m_items)))
    pieces, pairs = [], {}
    issues: list[Issue] = []
    piece_of: dict = {}  # model item -> the piece (frame) it was paired in
    def take(got, piece):
        piece.matched = len(got)
        pieces.append(piece)
        record(got, piece)
    def record(got, piece):
        for b, mi, d in got:
            pairs[mi] = b
            piece_of[mi] = piece
            a, c = b_items[b], m_items[mi]
            if a.family in c.families:
                c.family = a.family  # the design the file stands for here
            if a.key == c.key:
                continue
            if a.family == c.family and a.color == c.color:
                if "?" in (a.deco, c.deco):
                    continue  # a print we can't name: no news either way
                kind = "print"
            elif a.family == c.family:
                kind = "colour"
            elif a.color == c.color:
                kind = "part"
            else:
                kind = "part+colour"
            issues.append(Issue(kind, b, mi, step_of(b), distance=d))

    for k in range(max_pieces):
        if len(m_free) < MIN_PIECE:
            break
        share = (0.05, 0.8) if k == 0 else (0.8 + 0.02 * k, 0.8 + 0.02 * (k + 1))
        piece = register(b_items, m_items, b_free, m_free, share=share)
        if piece is None:
            break
        got = pair_up(b_items, m_items, b_free, m_free, piece)
        if sum(1 for b, mi, _ in got if b_items[b].family in m_items[mi].families) < MIN_PIECE:
            for b, mi, _ in got:  # not a real piece: give them back
                b_free.add(b)
                m_free.add(mi)
            break
        take(got, piece)


    if pieces:
        progress.stage("pairing")
        where = lambda mi: model.parts[m_items[mi].index].where
        # A top-level submodel placed a little off (a car a stud from where the instructions put
        # it): the main line-up pairs a few of its parts by chance and leaves the rest. When most
        # of it is left over, it is given back and lined up on its own below; if that fails,
        # its pairs are put back as they were.
        tops = defaultdict(set)
        for mi in range(len(m_items)):
            if len(where(mi)) >= 2:
                tops[where(mi)[:1]].add(mi)
        saved: dict = {}
        for g in tops.values():
            if len(g) < 6:
                continue
            same = sum(1 for mi in g if mi in pairs and b_items[pairs[mi]].key == m_items[mi].key)
            left = sum(1 for mi in g if mi not in pairs)
            if same < 0.5 * len(g) and left >= 0.25 * len(g):
                for mi in g:
                    if mi in pairs:
                        saved[mi] = (pairs.pop(mi), [x for x in issues if x.model == mi])
                        b_free.add(saved[mi][0])
                    m_free.add(mi)
                issues[:] = [x for x in issues if x.model not in g]
        # Submodels placed apart from the rest (a small piece lifted off), one at a time:
        # whole top-level submodels first, then each submodel on its own.
        groups = []
        for level in (lambda w: w[:1] if len(w) > 1 else w, lambda w: w[:-1]):
            by = defaultdict(set)
            for mi in m_free:
                by[level(where(mi))].add(mi)
            groups += sorted(by.values(), key=len, reverse=True)
        for gi, g in enumerate(groups):
            progress.tick(0.8 * gi / max(1, len(groups)), "submodels placed apart")
            g = g & m_free
            if len(g) < 3:
                continue
            a = yaw([model.parts[m_items[mi].index].M for mi in g])
            turn = turn_y(a) if abs(a) > 0.03 else None
            piece = register(b_items, m_items, b_free, g, min_piece=3, turn=turn)
            if piece is None:
                continue
            g_free = set(g)
            got = pair_up(b_items, m_items, b_free, g_free, piece)
            if sum(1 for b, mi, _ in got if b_items[b].key == m_items[mi].key) < max(3, len(g) // 2):
                for b, mi, _ in got:
                    b_free.add(b)
                continue
            m_free -= {mi for _, mi, _ in got}
            take(got, piece)
        for mi, (b, old) in saved.items():  # not lined up on its own after all: back as it was
            if mi in m_free and b in b_free:
                m_free.discard(mi)
                b_free.discard(b)
                pairs[mi] = b
                issues.extend(old)
        # Steps guide the rest. Each step of the model file (each submodel's step, per place it
        # is used) is linked to the instructions steps its parts were paired with by position:
        # one step of a model is usually one or two booklet steps, whatever its numbering. A part
        # is then compared first with the parts of its linked steps: one paired with a different
        # part, or left over, takes the same part and colour when that sits close by in a linked
        # step. Two parts in each other's places become two moved parts, not two wrong parts, a
        # missing one and an extra one.
        def mkey(mi):
            p = model.parts[m_items[mi].index]
            return (p.where[:-1], p.submodel, p.step)
        linked: dict = defaultdict(Counter)
        frames: dict = defaultdict(Counter)
        for mi, b in pairs.items():
            frames[mkey(mi)][id(piece_of[mi])] += 1
            linked[mkey(mi)][step_of(b)] += 1  # a wrong part still says which step this is
        by_id = {id(pc): pc for pc in pieces}
        main = max(pieces, key=lambda pc: pc.matched)
        def frame_of(mi):
            if mi in piece_of:
                return piece_of[mi]
            f = frames.get(mkey(mi))
            return by_id[f.most_common(1)[0][0]] if f else main
        progress.tick(0.82, "pairing step by step")
        wrong = {x.model for x in issues if x.kind in ("colour", "part", "part+colour")}
        pool_b = {pairs[mi] for mi in wrong} | b_free
        bpos = np.array([it.pos for it in b_items])
        grid = Grid(bpos, sorted(pool_b), 20.0)
        cand = []
        for mi in wrong | m_free:
            ok = linked.get(mkey(mi))
            if not ok:
                continue  # a step with nothing lined up: position alone, as before
            c, f = m_items[mi], frame_of(mi)
            q = c.pos @ f.R.T + f.t
            for b, d in grid.near(q, STEP_REACH):
                a = b_items[b]
                if a.family in c.families and a.color == c.color and step_of(b) in ok:
                    cand.append((d, b, mi))
        owner = {b: mi for mi, b in pairs.items()}
        new_b, new_m, got = set(), set(), []
        for d, b, mi in sorted(cand):
            if b not in new_b and mi not in new_m:
                new_b.add(b)
                new_m.add(mi)
                got.append((b, mi, d))
        # A part found in another part's place is reported as swapped (shown, never hidden as
        # "moved"); taking a part away from the model part paired with it is only done when that
        # one finds its own match too, else it keeps its pair (and its report) as it was.
        while True:
            nm = {mi for _, mi, _ in got}
            keep = [(b, mi, d) for b, mi, d in got if not (owner.get(b) not in (None, mi) and owner[b] not in nm)]
            if len(keep) == len(got):
                break
            got = keep
        new_b, new_m = {b for b, _, _ in got}, {mi for _, mi, _ in got}
        was = {mi: pairs[mi] for _, mi, _ in got if mi in pairs}  # where each part sat before
        dropped = set()
        for b, mi, d in got:
            if mi in pairs and pairs[mi] != b:  # its old (wrong) partner is free again
                dropped.add(mi)
                if pairs[mi] not in new_b:
                    b_free.add(pairs[mi])
            m2 = owner.get(b)
            if m2 is not None and m2 != mi and m2 not in new_m:  # the part's old (wrong) partner too
                dropped.add(m2)
                pairs.pop(m2, None)
                m_free.add(m2)
            pairs[mi] = b
            piece_of[mi] = frame_of(mi)
            b_free.discard(b)
            m_free.discard(mi)
        issues[:] = [x for x in issues if x.model not in dropped and x.model not in new_m]
        for b, mi, d in got:
            m_items[mi].family = b_items[b].family
            if mi in was and was[mi] != b:
                # in another part's place: a swap, shown as what the instructions put at that spot
                # against the part found there, with the step the part belongs to
                issues.append(Issue("swap", was[mi], mi, step_of(was[mi]), near=step_of(b), distance=d))
            elif b_items[b].key != m_items[mi].key and "?" not in (b_items[b].deco, m_items[mi].deco):
                issues.append(Issue("print", b, mi, step_of(b), distance=d))
            elif d > TOL:
                issues.append(Issue("offset", b, mi, step_of(b), distance=d))
        # what was freed may now sit on a part of its own (a different part in that spot)
        for f in pieces:
            loose = {mi for mi in m_free if frame_of(mi) is f}
            if loose:
                again = pair_up(b_items, m_items, b_free, loose, f)
                m_free -= {mi for _, mi, _ in again}
                record(again, f)

        # Left-over pairs of the same part and colour close together: placed a little differently
        # (a part the tables can't place exactly, a hair turned round), not a mistake.
        grid = Grid(bpos, sorted(b_free), 20.0)
        cand = []
        for mi in m_free:
            c = m_items[mi]
            for piece in pieces:
                q = c.pos @ piece.R.T + piece.t
                for b, d in grid.near(q, NEAR):
                    a = b_items[b]
                    if a.family in c.families and a.color == c.color:
                        cand.append((0 if step_of(b) in linked.get(mkey(mi), ()) else 1, d, b, mi))
        for _, d, b, mi in sorted(cand):
            if b in b_free and mi in m_free:
                b_free.discard(b)
                m_free.discard(mi)
                pairs[mi] = b
                m_items[mi].family = b_items[b].family
                issues.append(Issue("offset", b, mi, step_of(b), distance=d))
        grid = Grid(bpos, list(range(len(b_items))), 20.0)
        for mi in sorted(m_free):
            q = m_items[mi].pos @ main.R.T + main.t
            near = min(grid.near(q, NEAR), key=lambda x: x[1], default=None)
            issues.append(Issue("extra", None, mi, step_of(near[0]) if near else 0,
                                near=step_of(near[0]) if near else None, distance=near[1] if near else 0))
        for b in sorted(b_free):
            issues.append(Issue("missing", b, None, step_of(b)))
    issues.sort(key=lambda x: (x.step or 10 ** 9, x.kind))

    if not pieces:  # nothing lined up: the pairing stage never began
        progress.stage("pairing")
    progress.tick(0.9, "counting")
    # counts, like LDCad's check, by part and colour (prints are noted, not counted apart)
    rows: dict[tuple, CountRow] = {}
    for i, it in enumerate(b_items):
        r = rows.setdefault((it.family, it.color), CountRow(it.family, it.color, 0, 0))
        r.bi += 1
        r.bi_steps[step_of(i)] += 1
        r.prints_bi[it.deco] += 1
    for i, it in enumerate(m_items):
        r = rows.setdefault((it.family, it.color), CountRow(it.family, it.color, 0, 0))
        r.model += 1
        r.model_items.append(i)
        r.prints_model[it.deco] += 1
    counts = sorted(rows.values(), key=lambda r: (-(abs(r.bi - r.model)), names.get(r.family, r.family)))
    return Result(bi, model, m, b_items, m_items, issues, counts, pieces, pairs, skipped, names)

"""Compare a model with the instruction booklets (PDF): by steps and counts, no positions.

The totals come from the booklets' parts list, so a part is missing or extra only when the
model has a different number of it. The parts boxes say which step each part is in: every step
of the model (a submodel's "0 STEP") is linked to the booklet steps that hold the same parts,
rare parts counting more; equal parts are paired best-linked first; what is left over is
missing or extra, and left-overs in linked steps pair up as a wrong colour (same part) or a
wrong part (same colour). A right part in a wrong spot of its step can't be seen this way.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict

import numpy as np

from . import booklet, compare, ldraw, lxfml
from .elements import Elements
from .mapping import Mapping


TOOLS = {"96874", "31510"}   # the brick separator: in the parts list, never built in


def make_bi(b: booklet.Booklets, model: ldraw.Model, m: Mapping, title: str) -> tuple[lxfml.BI, Counter]:
    """The booklets as a BI (parts with their step, no positions); the parts list's parts the
    boxes don't show go into one last step, "not in a parts box". Also: elements not found."""
    E = Elements(m)
    prefer = set()
    for p in model.parts:
        prefer |= m.key_of_file(p.file)[0]
    unknown = Counter()
    box = b.box_total()
    # boxes may name an element more often than the list has it (a look-alike): drop the least sure
    over = {e: box[e] - b.inventory.get(e, 0) for e in box if box[e] > b.inventory.get(e, 0)}
    cut = Counter()
    for sc, k, i in sorted(((x[2], k, i) for k, s in enumerate(b.steps, start=1) for i, x in enumerate(s.parts) if x[0] in over),
                           reverse=True):
        e, q = b.steps[k - 1].parts[i][:2]
        take = min(q, over[e])
        if take > 0:
            cut[(k, i)] = take
            over[e] -= take
    nostep = len(b.steps) + 1
    parts = []
    found = {}

    def add(e, n, step, bag):
        if e not in found:
            found[e] = E.get(e, prefer)
        got = found[e]
        if got is None:
            unknown[e] += n
            return
        design, color = got
        if design.split(";")[0] in TOOLS:
            return
        for _ in range(n):
            parts.append(lxfml.BIPart(e, design, f"ldraw:{color}", "", np.eye(3), np.zeros(3), False, step,
                                      bag=str(bag) if bag else ""))
    for k, s in enumerate(b.steps, start=1):
        for i, (e, q, _) in enumerate(s.parts):
            if q - cut[(k, i)] > 0:
                add(e, q - cut[(k, i)], k, s.bag)
            box[e] -= cut[(k, i)]
    for e, n in b.inventory.items():
        rest = n - max(0, box[e])
        if rest > 0:
            add(e, rest, nostep, 0)
    books = len(b.files)
    st = [lxfml.Step(0, "", "")]
    for k, s in enumerate(b.steps, start=1):
        where = (f"Booklet {s.book + 1}, page {s.page}" if books > 1 else f"Page {s.page}")
        st.append(lxfml.Step(k, "", where, label=str(s.number)))
    st.append(lxfml.Step(nostep, "", "Not in a parts box", label="?"))
    bi = lxfml.BI(parts, st, [os.path.basename(f) for f in b.files], title)
    return bi, unknown


def match(bi: lxfml.BI, model: ldraw.Model, m: Mapping) -> compare.Result:
    b_items, m_items, skipped, names = compare.build_items(bi, model, m)
    nostep = len(bi.steps) - 1
    # One shape can have several numbers (a newer mould, Rebrickable's design vs LEGO's): compare
    # under the number the model's part goes by. Prints are not compared here.
    alias = {}
    for it in m_items:
        alias.setdefault(it.family, it.family)
    for it in m_items:
        for d in it.families:
            alias.setdefault(d, it.family)
    key = lambda it: (alias.get(it.family, it.family), it.color)
    step_of = lambda b: bi.parts[b_items[b].index].step
    group_of = {}
    for mi, it in enumerate(m_items):
        p = model.parts[it.index]
        group_of[mi] = (p.where[:-1], p.submodel, p.step)
    P = defaultdict(Counter)   # booklet step -> key -> n
    M = defaultdict(Counter)   # model step -> key -> n
    for b, it in enumerate(b_items):
        P[step_of(b)][key(it)] += 1
    for mi, it in enumerate(m_items):
        M[group_of[mi]][key(it)] += 1
    total = Counter(key(it) for it in b_items)
    w = {x: 1.0 / n for x, n in total.items()}

    # the model's steps in build order, to break ties between equally good links (two booklet
    # steps with the same parts): the earlier model step with the earlier booklet step
    first = {}
    for mi, it in enumerate(m_items):
        g, gs = group_of[mi], model.parts[it.index].global_step
        first[g] = min(first.get(g, gs), gs)
    rank = {g: i / max(1, len(first) - 1) for i, g in enumerate(sorted(first, key=lambda g: (first[g], str(g))))}
    K = max(1, nostep - 2)

    def smooth(L):
        # neighbouring booklet steps are usually built in the same model step: a step gains half
        # its better neighbour's link (the better one only, so a last step isn't worse off)
        out = defaultdict(float)
        for (k, g), v in L.items():
            out[(k, g)] += v
            for dk in (-1, 1):
                out[(k + dk, g)] += 0.0
        for (k, g) in list(out):
            out[(k, g)] += 0.5 * max(L.get((k - 1, g), 0.0), L.get((k + 1, g), 0.0))
        for (k, g) in out:
            out[(k, g)] += 1e-7 * (1 - abs((k - 1) / K - rank[g]))
        return out

    # 1. a first link between booklet steps and model steps: shared parts, rare ones count more
    L = defaultdict(float)
    groups_with = defaultdict(list)
    for g, c in M.items():
        for x in c:
            groups_with[x].append(g)
    for k, c in P.items():
        if k == nostep:
            continue
        for x, n in c.items():
            for g in groups_with.get(x, ()):
                L[(k, g)] += min(n, M[g][x]) * w[x]
    Ls = smooth(L)
    # 2. pair equal parts, the best-linked booklet step and model step first
    pu, mu = defaultdict(list), defaultdict(list)
    for b, it in enumerate(b_items):
        pu[key(it)].append(b)
    for mi, it in enumerate(m_items):
        mu[key(it)].append(mi)
    def pair_all(Ls):
        pairs: dict[int, int] = {}
        left_b, left_m = [], []
        for x in set(pu) | set(mu):
            bs, ms = pu.get(x, []), mu.get(x, [])
            cand = sorted(((Ls.get((step_of(b), group_of[mi]), 0.0), b, mi) for b in bs for mi in ms), key=lambda t: -t[0])
            ub, um = set(), set()
            for v, b, mi in cand:
                if b in ub or mi in um:
                    continue
                ub.add(b); um.add(mi)
                pairs[mi] = b
            left_b += [b for b in bs if b not in ub]
            left_m += [mi for mi in ms if mi not in um]
        return pairs, left_b, left_m

    def links(pairs):
        """Which booklet steps each model step was built from, from the pairs (a sub-build built
        several times gets its own booklet steps for each copy)."""
        PL = defaultdict(float)
        for mi, b in pairs.items():
            if step_of(b) != nostep:
                PL[(step_of(b), group_of[mi])] += w[key(b_items[b])]
        return smooth(PL)

    pairs, left_b, left_m = pair_all(Ls)
    Ls = links(pairs)
    # 3. pair again with those links: which of several equal parts is the one left over follows them
    pairs, left_b, left_m = pair_all(Ls)
    Ls = links(pairs)
    top = defaultdict(float)
    by_group = defaultdict(list)
    for (k, g), v in Ls.items():
        top[g] = max(top[g], v)
        by_group[g].append((v, k))
    # 4. left-overs in linked steps: a different colour, then a different part, is a mistake
    issues = []
    used_m = set()
    for rank in ("colour", "part"):
        cand = []
        for b in left_b:
            pi, k = b_items[b], step_of(b)
            for mi in left_m:
                if mi in used_m:
                    continue
                it = m_items[mi]
                same = alias.get(pi.family, pi.family) == it.family or pi.family in it.families
                if rank == "colour" and not (same and pi.color != it.color):
                    continue
                if rank == "part" and not (not same and pi.color == it.color):
                    continue
                g = group_of[mi]
                v = Ls.get((k, g), 0.0)
                if v > 0 and v >= 0.4 * top[g]:
                    cand.append((v, b, mi))
                elif k == nostep and rank == "colour":   # a parts-box part we couldn't place
                    cand.append((1e-6, b, mi))
        cand.sort(key=lambda t: -t[0])
        ub = set()
        for v, b, mi in cand:
            if b in ub or mi in used_m:
                continue
            ub.add(b); used_m.add(mi)
            # the step to show: of the booklet steps that use the wanted part, the one best linked
            # to the model part's step (equal parts in several steps can be left over in any of them)
            x, g = key(b_items[b]), group_of[mi]
            uses = [(v, kk) for v, kk in by_group.get(g, []) if P[kk].get(x)]
            k = max(uses)[1] if uses else step_of(b)
            if k == nostep:
                k = max(by_group.get(g, [(0, nostep)]))[1]
            issues.append(compare.Issue(rank, b, mi, k))
        left_b = [b for b in left_b if b not in ub]
    # booklet steps built in a minifigure submodel (a dress built from plates in the booklet,
    # one part in the model): what is missing there is a figure difference
    fig_subs = {model.parts[it.index].submodel for it in m_items
                if (m.lib.description(it.file) or "").lstrip("~").lower().startswith("minifig torso")}
    fig_group = {g: any(w[0] in fig_subs for w in g[0]) or g[1] in fig_subs for g in by_group}
    best_group = {}
    for (k, g), v in Ls.items():
        if v > best_group.get(k, (0, None))[0]:
            best_group[k] = (v, g)
    # ... or a booklet step next to one with minifig parts (the dress before the torso)
    fig_k = {step_of(b) for b, it in enumerate(b_items)
             if (m.lib.description(it.file) or "").lstrip("~").lower().startswith("minifig")}
    # ... or a small step of one colour, the colour of a figure's torso, skirt or legs (in the
    # booklets or the model): a brick-built dress (curved slope, bracket, plate) whose figure is
    # shown apart from the steps (newer skirts have no LDraw part, so the model's figures count too)
    desc = lambda it: (m.lib.description(it.file) or m.lib.description(it.family + ".dat")
                       or "").lstrip("~").lower()
    body = ("minifig torso", "minifig hips", "minifig legs", "minifig skirt")
    skirts = {it.color for it in list(b_items) + list(m_items) if desc(it).startswith(body)}
    in_step, dress_k = defaultdict(list), set()
    for b, it in enumerate(b_items):
        in_step[step_of(b)].append(it)
    for k, its in in_step.items():
        if (k != nostep and len(its) <= 6 and len({it.color for it in its}) == 1
                and its[0].color in skirts and not any(desc(it).startswith("minifig") for it in its)):
            dress_k.add(k)
    # a dress part left over among the parts no box shows: the dress's own part went to the
    # model's equal part elsewhere, so the shortfall lands here
    dress_keys = {key(it) for k in dress_k for it in in_step[k]}
    for b in left_b:
        k = step_of(b)
        g = best_group.get(k, (0, None))[1]
        near_fig = (k in dress_k or (k == nostep and key(b_items[b]) in dress_keys)
                    or k != nostep and any(k + d in fig_k for d in (-2, -1, 0, 1, 2)))
        issues.append(compare.Issue("missing", b, None, k, fig=bool(g and fig_group.get(g)) or near_fig))
    for mi in left_m:
        if mi in used_m:
            continue
        v, k = max(by_group.get(group_of[mi], [(0.0, 0)]))
        issues.append(compare.Issue("extra", None, mi, k, near=k or None))
    issues.sort(key=lambda x: (x.step or 10 ** 9, x.kind))
    # counts by part and colour, as in compare
    rows = {}
    for b, it in enumerate(b_items):
        fam = alias.get(it.family, it.family)
        r = rows.setdefault((fam, it.color), compare.CountRow(fam, it.color, 0, 0))
        r.bi += 1
        r.bi_steps[step_of(b)] += 1
        r.prints_bi[it.deco] += 1
    for mi, it in enumerate(m_items):
        fam = alias.get(it.family, it.family)
        r = rows.setdefault((fam, it.color), compare.CountRow(fam, it.color, 0, 0))
        r.model += 1
        r.model_items.append(mi)
        r.prints_model[it.deco] += 1
    counts = sorted(rows.values(), key=lambda r: (-(abs(r.bi - r.model)), names.get(r.family, r.family)))
    return compare.Result(bi, model, m, b_items, m_items, issues, counts, [], pairs, skipped, names)

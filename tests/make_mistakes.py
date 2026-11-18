"""Make a test model with known mistakes from a correct one, and check what bicheck finds.

    python tests/make_mistakes.py correct.mpd instructions.lxfml [--seed 1]

The mistakes are the ones builders make reading instructions: a colour taken for its
neighbour (light/dark bluish grey, tan/dark tan), a plate for a tile, a part left out, a
part too many. On top, the whole model is turned a quarter round and moved, and one
submodel is lifted off (like a floor lifted to show the room below), so bicheck has to
find both frames. Deleted lines become comments, so line numbers stay where they were.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bicheck import compare, ldraw, lxfml, mapping, report  # noqa: E402

COLOURS = {71: 72, 72: 71, 19: 28, 28: 19, 70: 308, 308: 70, 15: 503, 0: 72, 4: 320, 320: 4, 1: 272, 2: 288,
           14: 191, 25: 191, 378: 151}
PARTS = {"3023.dat": "3069b.dat", "3069b.dat": "3023.dat", "3024.dat": "3070b.dat", "3070b.dat": "3024.dat",
         "3022.dat": "3068b.dat", "3068b.dat": "3022.dat", "3710.dat": "63864.dat", "63864.dat": "3710.dat",
         "3623.dat": "3666.dat"}


def mutate(path: str, out: str, seed: int, n_colour=25, n_part=12, n_delete=8, n_add=4):
    rnd = random.Random(seed)
    lines = open(path, encoding="utf-8").read().splitlines()
    files = {l[7:].strip().lower() for l in lines if l.upper().startswith("0 FILE ")}
    main = next(l[7:].strip() for l in lines if l.upper().startswith("0 FILE "))
    # submodels the main model uses (tex2ldraw also writes alternate views nothing references)
    refs, cur = {}, None
    for l in lines:
        if l.upper().startswith("0 FILE "):
            cur = l[7:].strip().lower()
        f = l.split(None, 14)
        if len(f) == 15 and f[0] == "1" and f[14].lower() in files:
            refs.setdefault(cur, set()).add(f[14].lower())
    reach, todo = set(), [main.lower()]
    while todo:
        x = todo.pop()
        if x not in reach:
            reach.add(x)
            todo += refs.get(x, ())
    cur, part_lines, sub_refs = None, [], []
    for i, l in enumerate(lines):
        if l.upper().startswith("0 FILE "):
            cur = l[7:].strip()
        f = l.split(None, 14)
        if len(f) == 15 and f[0] == "1":
            if f[14].lower() in files:
                if cur == main:
                    sub_refs.append(i)
            elif cur.lower() in reach:
                part_lines.append((i, cur))
    rnd.shuffle(part_lines)
    truth = []
    used = set()

    def take(pred):
        for i, sub in part_lines:
            if i not in used and pred(lines[i].split(None, 14)):
                used.add(i)
                return i, sub
        return None, None

    for _ in range(n_colour):
        i, sub = take(lambda f: f[1].isdigit() and int(f[1]) in COLOURS)
        if i is None:
            break
        f = lines[i].split(None, 14)
        new = COLOURS[int(f[1])]
        truth.append({"kind": "colour", "sub": sub, "line": i + 1, "file": f[14], "was": int(f[1]), "now": new})
        f[1] = str(new)
        lines[i] = " ".join(f)
    for _ in range(n_part):
        i, sub = take(lambda f: f[14].lower() in PARTS)
        if i is None:
            break
        f = lines[i].split(None, 14)
        new = PARTS[f[14].lower()]
        truth.append({"kind": "part", "sub": sub, "line": i + 1, "was": f[14], "now": new, "color": f[1]})
        f[14] = new
        lines[i] = " ".join(f)
    for _ in range(n_delete):
        i, sub = take(lambda f: True)
        f = lines[i].split(None, 14)
        truth.append({"kind": "missing", "sub": sub, "line": i + 1, "file": f[14], "color": f[1]})
        lines[i] = "0 // deleted: " + lines[i]
    # a floor lifted off: one big submodel reference in the main model moved up
    lifted = max(sub_refs, key=lambda i: 0, default=None)
    if sub_refs:
        lifted = rnd.choice(sub_refs[len(sub_refs) // 3:])
        f = lines[lifted].split(None, 14)
        f[3] = str(float(f[3]) - 480)  # 20 bricks up
        f[4] = str(float(f[4]) + 600)
        lines[lifted] = " ".join(f)
    wrap = ["0 FILE test-wrapper.ldr", "0 Test wrapper", "0 Name: test-wrapper.ldr",
            f"1 16 137 -40 -611 0 0 1 0 1 0 -1 0 0 {main}", "0 STEP"]
    for k in range(n_add):  # extra parts the instructions don't have, far from the model
        wrap.append(f"1 72 {800 + 40 * k} -8 0 1 0 0 0 1 0 0 0 1 3023.dat")
        truth.append({"kind": "extra", "sub": "test-wrapper.ldr", "line": len(wrap)})
    # the wrapper's line numbers: it comes first in the new file
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(wrap + lines) + "\n")
    for t in truth:  # lines of the original file moved down by the wrapper
        if t["sub"] != "test-wrapper.ldr":
            t["line"] += len(wrap)
    return truth


def check(model_path: str, lxfml_path: str, truth: list) -> dict:
    bi = lxfml.load(lxfml_path)
    model = ldraw.read(model_path)
    m = mapping.load()
    r = compare.compare(bi, model, m)
    d = report.data(r)
    by_line = {}
    for x in d["issues"]:
        if "where" in x:
            by_line[(x["where"]["submodel"].lower(), x["where"]["line"])] = x
    found = {"colour": 0, "part": 0, "missing": 0, "extra": 0}
    total = {k: sum(1 for t in truth if t["kind"] == k) for k in found}
    wrong = []
    for t in truth:
        if t["kind"] in ("colour", "part", "extra"):
            x = by_line.get((t["sub"].lower(), t["line"]))
            ok = x is not None and (x["kind"] in (t["kind"], "swap") or (t["kind"] == "part" and x["kind"] == "part+colour"))
            found[t["kind"]] += ok
            if not ok:
                wrong.append((t, x and x["kind"]))
    miss = Counter_missing = {}
    for x in d["issues"]:
        if x["kind"] == "missing":
            k = (x["want"]["color"]["code"],)
            miss[k] = miss.get(k, 0) + 1
    for t in truth:
        if t["kind"] == "missing":
            k = (int(t["color"]) if t["color"].isdigit() else t["color"],)
            if miss.get(k, 0) > 0:
                miss[k] -= 1
                found["missing"] += 1
            else:
                wrong.append((t, None))
    reported = {k: sum(1 for x in d["issues"] if x["kind"] == k) for k in ("colour", "part", "part+colour", "missing", "extra", "print", "offset", "swap")}
    return {"found": found, "total": total, "reported": reported, "lined_up": d["summary"]["lined_up"],
            "pieces": d["summary"]["pieces"], "not_found": wrong[:20]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("instructions")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    out = a.out or os.path.splitext(a.model)[0] + f".mistakes{a.seed}.mpd"
    truth = mutate(a.model, out, a.seed)
    with open(out + ".truth.json", "w", encoding="utf-8") as f:
        json.dump(truth, f, indent=1)
    res = check(out, a.instructions, truth)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()

"""The comparison as data (JSON-ready), as text for the console, and as one self-contained HTML page."""
from __future__ import annotations

import html
import json
import os
import re
from collections import Counter

from .compare import Result

KIND_TEXT = {
    "colour": "Wrong colour",
    "part": "Wrong part",
    "part+colour": "Wrong part and colour",
    "print": "Wrong print",
    "missing": "Missing",
    "extra": "Extra",
    "offset": "Placed a little differently",
    "swap": "Swapped with a part nearby",
}


def _color(r: Result, c) -> dict:
    m = r.mapping
    return {"name": m.color_name(c), "rgb": m.color_rgb.get(c, "#888888") if not isinstance(c, str) else "#888888",
            "code": c if not isinstance(c, str) else None}


def _part(r: Result, family: str, deco: str = "", file: str = "") -> dict:
    f = file or r.names.get(family) or (family[2:] if family.startswith("f:") else family) + ".dat"
    desc = r.mapping.lib.description(f)
    if not desc:  # a print the library lacks (973pcw): the plain part's name ("Minifig Torso")
        m = re.fullmatch(r"(\d+[a-z]?\d*?)(?:p[a-z0-9]{2,6}|pr\d{4}|d[a-z0-9]{2,4}|c[a-z0-9]{2,4})", f.lower().removesuffix(".dat"))
        base = r.mapping.lib.description(m.group(1) + ".dat") if m else ""
        desc = f"{base} (print)" if base else ""
    out = {"file": f.lower().removesuffix(".dat"), "desc": desc}
    if deco:
        out["print"] = "unknown print" if deco == "?" else f"print {deco}"
    return out


def _prints(c: Counter) -> str:
    if set(c) <= {""}:
        return ""
    return ", ".join(f"{q}x " + ("plain" if d == "" else "unknown print" if d == "?" else f"print {d}")
                     for d, q in c.most_common())


def _where(r: Result, mi: int) -> dict:
    p = r.model.parts[r.model_items[mi].index]
    chain = [w[0] for w in p.where]
    return {"chain": chain, "submodel": p.submodel, "step": p.step, "line": p.line, "global": p.global_step}


def _step(r: Result, n: int) -> dict:
    s = r.bi.steps[n] if 0 < n < len(r.bi.steps) else None
    if s is None:
        return {"n": 0, "path": "", "book": "", "bag": "", "label": ""}
    bags = Counter(p.bag for p in r.bi.parts if p.step == n and p.bag)
    return {"n": n, "path": s.path, "book": s.book if len(r.bi.books) > 1 else "",
            "bag": bags.most_common(1)[0][0] if bags else "", "label": getattr(s, "label", "")}


def _is_fig(desc: str) -> bool:
    return desc.lstrip("~").lower().startswith(("minifig", "figure"))


def data(r: Result) -> dict:
    steps_used: set[int] = set()
    issues = []
    # steps that build a minifigure: their unnamed parts (prints LDraw lacks) are figure parts too
    fig_steps = {r.bi.parts[it.index].step for it in r.bi_items
                 if _is_fig(_part(r, it.family, it.deco)["desc"])}
    # submodels of the model that are figures (a figure and what it holds: "annabates.ldr"):
    # a minifig torso in it, not just minifig-sized plants or tools
    fig_subs = set()
    for it in r.model_items:
        desc = _part(r, it.family, it.deco, it.file)["desc"].lstrip("~").lower()
        if desc.startswith("minifig torso"):
            fig_subs.add(r.model.parts[it.index].submodel)
    for x in r.issues:
        d = {"kind": x.kind, "step": x.step, "near": x.near, "distance": round(x.distance, 1)}
        if x.bi is not None:
            b = r.bi_items[x.bi]
            d["want"] = {**_part(r, b.family, b.deco), "color": _color(r, b.color), "deco": b.deco}
        if x.model is not None:
            mi = r.model_items[x.model]
            d["have"] = {**_part(r, mi.family, mi.deco, mi.file), "color": _color(r, mi.color), "deco": mi.deco}
            d["where"] = _where(r, x.model)
        descs = [d[k]["desc"] for k in ("want", "have") if k in d]
        in_fig = x.model is not None and any(w in fig_subs for w in d["where"]["chain"])
        if any(_is_fig(t) for t in descs) or (x.step in fig_steps and not all(descs)) or in_fig or getattr(x, "fig", False):
            d["fig"] = True
        steps_used.add(x.step)
        issues.append(d)
    counts = []
    for c in r.counts:
        counts.append({**_part(r, c.family), "color": _color(r, c.color), "bi": c.bi, "model": c.model,
                       "prints_bi": _prints(c.prints_bi), "prints_model": _prints(c.prints_model),
                       "steps": sorted([n, q] for n, q in c.bi_steps.items()),
                       "where": [_where(r, mi) for mi in c.model_items]})
    # what each step with an issue holds, so it is easy to find in the booklet
    contents: dict[int, Counter] = {n: Counter() for n in steps_used if n}
    for it in r.bi_items:
        n = r.bi.parts[it.index].step
        if n in contents:
            contents[n][(it.family, it.deco, it.color)] += 1
    steps = {}
    for n in range(1, len(r.bi.steps)):
        s = _step(r, n)
        if n in contents:
            s["contents"] = [{**_part(r, f, d), "color": _color(r, c), "qty": q}
                             for (f, d, c), q in sorted(contents[n].items(), key=lambda kv: -kv[1])]
        steps[n] = s
    kinds = Counter(x.kind for x in r.issues)
    return {
        "model": os.path.basename(r.model.path),
        "instructions": r.bi.title,
        "summary": {
            "bi_parts": len(r.bi_items), "model_parts": len(r.model_items), "steps": len(r.bi.steps) - 1,
            "paired": len(r.pairs), "lined_up": round(100 * r.lined_up, 1), "pieces": len(r.pieces),
            "kinds": dict(kinds), "skipped": dict(r.skipped),
            "figs": sum(1 for x in issues if x.get("fig")),
            "count_diffs": sum(1 for c in r.counts if c.bi != c.model),
            "mapping": r.mapping.sources, "library": r.mapping.lib.path,
        },
        "issues": issues, "counts": counts, "steps": steps,
    }


# -- console ------------------------------------------------------------------------------
def _pname(p: dict) -> str:
    return p["file"] + (f" [{p['print']}]" if p.get("print") else "")


def _cname(c: dict) -> str:
    return f"{c['name']} ({c['code']})" if c.get("code") is not None else c["name"]


def _sn(d: dict, n) -> str:
    """A step as printed: its number, with the booklet ("2:97") when there are several."""
    st = d["steps"].get(n, {}) or {}
    if not st.get("label"):
        return str(n)
    b = re.match(r"Booklet (\d+)", st.get("book", ""))
    return f"{b[1]}:{st['label']}" if b else st["label"]


def _loc(w: dict) -> str:
    return f"{' > '.join(w['chain'])}, step {w['step']}, line {w['line']}"


def text(d: dict, limit: int = 40) -> str:
    s = d["summary"]
    out = [f"{d['model']}: {s['model_parts']} parts; instructions: {s['bi_parts']} parts in {s['steps']} steps."]
    if s.get("mode") == "booklets":
        out.append(f"Compared with the booklets {', '.join(s.get('booklets', []))} by step and part count; "
                   f"{s['lined_up']}% of the parts matched.")
    elif s["paired"]:
        out.append(f"Lined up {s['lined_up']}% of the parts ({s['pieces']} piece{'s' * (s['pieces'] != 1)}).")
    else:
        out.append("Could not line the model up with the instructions: only the counts below.")
    if s["kinds"]:
        out.append("Found: " + ", ".join(f"{n} {KIND_TEXT[k].lower()}" for k, n in s["kinds"].items()))
    main = [x for x in d["issues"] if x["kind"] not in ("print", "offset") and not x.get("fig")]
    shown = main[:limit]
    if shown:
        out.append("")
        out.append("By step (booklet:step as printed):" if s.get("mode") == "booklets" and len(s.get("booklets", [])) > 1
                   else "By step (instructions step numbers):")
    for x in shown:
        st = d["steps"].get(x["step"], {})
        head = f"  step {_sn(d, x['step']):>4}" + (f" [{st.get('bag')}]" if st.get("bag") else "") if x["step"] else "  (no step) "
        if x["kind"] == "missing":
            w = x["want"]
            out.append(f"{head}  MISSING  {_pname(w)} {_cname(w['color'])}  {w['desc']}")
        elif x["kind"] == "extra":
            h = x["have"]
            near = f" (near step {_sn(d, x['near'])})" if x.get("near") else ""
            out.append(f"{head}  EXTRA    {_pname(h)} {_cname(h['color'])}{near}  at {_loc(x['where'])}")
        else:
            w, h = x["want"], x["have"]
            own = f" (it belongs {x['distance']:.0f} LDU away, step {_sn(d, x['near'])})" if x["kind"] == "swap" else ""
            out.append(f"{head}  {KIND_TEXT[x['kind']].upper()}: wants {_pname(w)} {_cname(w['color'])}, "
                       f"model has {_pname(h)} {_cname(h['color'])}{own}  at {_loc(x['where'])}")
    rest = len(main) - len(shown)
    if rest > 0:
        out.append(f"  ... and {rest} more in the report")
    figs = [x for x in d["issues"] if x.get("fig") and x["kind"] not in ("print", "offset")]
    if figs:
        fsteps = sorted({x["step"] for x in figs if x["step"]})
        out.append(f"  ({len(figs)} minifigure part differences, in steps {', '.join(_sn(d, n) for n in fsteps[:10])}"
                   f"{' ...' if len(fsteps) > 10 else ''}: in the report. Figures are often left out or built differently.)")
    if s["kinds"].get("print"):
        out.append(f"  ({s['kinds']['print']} print differences, e.g. a plain part where the instructions have a print: in the report)")
    diffs = [c for c in d["counts"] if c["bi"] != c["model"]]
    if diffs:
        out.append("")
        out.append("Count differences (model - instructions):")
        for c in diffs[:limit]:
            steps = ", ".join(_sn(d, n) + (f"x{q}" if q > 1 else "") for n, q in c["steps"][:12])
            more = " ..." if len(c["steps"]) > 12 else ""
            out.append(f"  {c['model'] - c['bi']:+4d}  {c['file']:<14} {_cname(c['color']):<28} "
                       f"BI {c['bi']:>3}, model {c['model']:>3}   steps {steps}{more}")
    return "\n".join(out)


def find(d: dict, part: str, color: str = "") -> str:
    """Every step that uses a part (and colour): the answer to 'where does this go?'."""
    part, color = part.lower().removesuffix(".dat"), color.lower()
    rows = [c for c in d["counts"] if (part in c["file"] or part in c["desc"].lower()) and
            (not color or color == str(c["color"].get("code")) or color in c["color"]["name"].lower())]
    if not rows:
        return f"No {part} {color} in the instructions or the model."
    out = []
    for c in rows:
        out.append(f"{c['file']} {_cname(c['color'])} - {c['desc']}: instructions {c['bi']}, model {c['model']}")
        for n, q in c["steps"]:
            st = d["steps"].get(n, {})
            out.append(f"    step {_sn(d, n):>4}  x{q}" + (f"  [{st.get('bag')}]" if st.get("bag") else ""))
        for w in c["where"]:
            out.append(f"    model: {_loc(w)}")
    return "\n".join(out)


# -- HTML ---------------------------------------------------------------------------------
def thumbnails(d: dict, lib) -> dict:
    """A picture of every part in every colour the report shows: "file|code" -> data: URI."""
    from . import progress, thumbs
    keys = set()
    for x in d["issues"]:
        for s in ("want", "have"):
            if s in x:
                keys.add((x[s]["file"], x[s]["color"]["code"]))
    for c in d["counts"]:
        keys.add((c["file"], c["color"]["code"]))
    for s in d["steps"].values():
        for c in s.get("contents", []):
            keys.add((c["file"], c["color"]["code"]))
    r = thumbs.Renderer(lib)
    out = {}
    keys = sorted(k for k in keys if k[1] is not None)
    for i, (f, c) in enumerate(keys):
        if i % 10 == 0:
            progress.tick(i / max(1, len(keys)), f"part pictures {i} of {len(keys)}")
        uri = r.data_uri(f + ".dat", c)
        if not uri:  # a print the library lacks: the plain part
            m = re.fullmatch(r"(\d+[a-z]?\d*?)(?:p[a-z0-9]{2,6}|pr\d{4}|d[a-z0-9]{2,4}|c[a-z0-9]{2,4})", f)
            uri = r.data_uri(m.group(1) + ".dat", c) if m else ""
        if uri:
            out[f"{f}|{c}"] = uri
    return out


def write_html(d: dict, path: str, lib=None) -> None:
    if lib is not None and "thumbs" not in d:
        d = {**d, "thumbs": thumbnails(d, lib)}
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "report.html"), encoding="utf-8") as f:
        page = f.read()
    blob = json.dumps(d, separators=(",", ":")).replace("</", "<\\/")
    page = page.replace("__TITLE__", html.escape(f"{d['model']} vs instructions"))
    page = page.replace("__DATA__", blob)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)

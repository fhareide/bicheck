"""A set's instruction booklets (the PDFs from lego.com): every step and the parts in its box.

What a booklet holds that a program can read:
- the parts list on the last pages: each part's picture, "4x" and its element number below;
- each step's number (the most common big number style in the booklet), and above it a box
  with the step's new parts: a picture each with "2x" below (totals: a sub-build built 4 times
  shows 4 times the parts);
- a bag's number when a new bag is opened (a much bigger number).
A parts-box picture is named by comparing it with the parts-list pictures; an element is used
no more often than the parts list says, so look-alikes go to the part still unaccounted for.

Steps are numbered per booklet; a set with several booklets is read in order (by the first bag
each booklet opens). The result is cached beside bicheck's other downloads.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

QTY = re.compile(r"^(\d{1,3})x$")
ELEMENT = re.compile(r"^\d{6,8}$")
N = 24            # pictures are compared at N x N
VERSION = 3       # of the cached result
PAGE_PX = 1000    # px, the width of a booklet page picture in the report


@dataclass
class Step:
    book: int        # 0-based, in reading order
    page: int        # 1-based
    number: int      # as printed
    bag: int = 0
    parts: list = field(default_factory=list)   # [(element, qty, sureness 0 = sure .. 1)]
    rect: tuple = ()  # where the step number is printed on its page (points)


@dataclass
class Booklets:
    files: list
    steps: list                  # [Step]
    inventory: dict              # element -> qty (the parts list)

    def box_total(self) -> Counter:
        c = Counter()
        for s in self.steps:
            for e, q, _ in s.parts:
                c[e] += q
        return c


def _fitz():
    try:
        import pymupdf
    except ImportError:  # older name
        import fitz as pymupdf
    return pymupdf


def _spans(page):
    seen = set()   # the same label is sometimes drawn more than once on top of itself
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                t = s["text"].strip()
                if not t:
                    continue
                r = s["bbox"]
                k = (t, round(r[0]), round(r[1]))
                if k not in seen:
                    seen.add(k)
                    yield t, round(s["size"], 1), r, s["font"]


def _images(page):
    out = []
    for im in page.get_images(full=True):
        for r in page.get_image_rects(im[0]):
            if 4 <= r.width < 160 and 4 <= r.height < 120:
                out.append((im[0], (r.x0, r.y0, r.x1, r.y1)))
    return out


def _image_above(imgs, word, gap=12):
    """The picture a "2x" label belongs to: just above it, starting at the label's left edge."""
    best = None
    for x, r in imgs:
        dy = word[1] - r[3]
        if -3 <= dy <= gap and r[0] - 4 <= word[0] <= r[2]:
            k = dy + 0.5 * abs(r[0] - word[0])   # a long plate's picture can reach over its neighbour's label
            if best is None or k < best[0]:
                best = (k, x, r)
    return best


def _overlap(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


class _Looks:
    def __init__(self, docs):
        self.docs, self.cache = docs, {}

    def get(self, key):
        if key in self.cache:
            return self.cache[key]
        fz = _fitz()
        doc = self.docs[key[0]]
        try:
            pix = fz.Pixmap(doc, key[1])
            info = doc.extract_image(key[1])
            if info.get("smask"):
                pix = fz.Pixmap(pix, fz.Pixmap(doc, info["smask"]))
            if pix.colorspace and pix.colorspace.n != 3:
                pix = fz.Pixmap(fz.csRGB, pix)
        except Exception:
            self.cache[key] = None
            return None
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n).astype(np.float32) / 255
        rgb, alpha = a[..., :3], (a[..., 3:4] if pix.n == 4 else None)
        if alpha is not None:
            rgb = rgb * alpha + (1 - alpha)
        bg = rgb[0, 0]
        ink = np.abs(rgb - bg).sum(-1) > 0.08
        if ink.any():   # cropped to the part, on white
            ys, xs = np.where(ink)
            rgb = rgb[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
            rgb = np.where((np.abs(rgb - bg).sum(-1) > 0.08)[..., None], rgb, 1.0)
        h, w = rgb.shape[:2]
        out = (rgb[(np.arange(N) * h / N).astype(int)][:, (np.arange(N) * w / N).astype(int)], np.log(w / h))
        self.cache[key] = out
        return out


def _distance(a, b) -> float:
    return float(np.abs(a[0] - b[0]).mean() + 0.15 * abs(a[1] - b[1]))


def _styles(doc):
    """(step number size, its font, parts-box label size) as this booklet prints them."""
    digits, labels = Counter(), Counter()
    for page in doc:
        spans = list(_spans(page))
        if sum(1 for t, *_ in spans if ELEMENT.match(t)) >= 5:
            continue   # the parts list prints its own labels
        for t, size, r, font in spans:
            if t.isdigit() and size >= 14:
                digits[(size, font)] += 1
            elif QTY.match(t):
                labels[size] += 1
    (step_size, step_font), _ = digits.most_common(1)[0] if digits else ((26.0, ""), 0)
    label = labels.most_common(1)[0][0] if labels else 8.0
    return step_size, step_font, label


def is_booklet(path: str) -> bool:
    """A LEGO instructions PDF: some pages of big step numbers with "2x" labels."""
    try:
        doc = _fitz().open(path)
    except Exception:
        return False
    n = 0
    for page in list(doc)[: min(30, doc.page_count)]:
        n += sum(1 for t, *_ in _spans(page) if QTY.match(t))
    return n >= 10


def _order(docs) -> list[int]:
    """Booklets in reading order: by the first bag number each opens, then as given."""
    firsts = []
    for i, doc in enumerate(docs):
        step_size, _, _ = _styles(doc)
        first = None
        for page in list(doc)[:40]:
            bags = [int(t) for t, size, r, f in _spans(page) if t.isdigit() and size >= step_size * 1.3]
            if bags:
                first = bags[0]
                break
        firsts.append((first if first is not None else 10 ** 6, i))
    return [i for _, i in sorted(firsts)]


def read(paths: list[str], tick=lambda frac, detail="": None) -> Booklets:
    fz = _fitz()
    docs = [fz.open(p) for p in paths]
    order = _order(docs)
    paths, docs = [paths[i] for i in order], [docs[i] for i in order]
    total_pages = sum(d.page_count for d in docs) or 1
    looks = _Looks(docs)
    # 1. the parts list
    pics: dict[tuple, str] = {}
    inventory: Counter = Counter()
    list_pages = set()
    for bi, doc in enumerate(docs):
        for pno in range(doc.page_count):
            spans = list(_spans(doc[pno]))
            ids = [(t, r) for t, size, r, f in spans if ELEMENT.match(t)]
            if len(ids) < 5:
                continue
            list_pages.add((bi, pno))
            imgs = _images(doc[pno])
            qtys = [(int(QTY.match(t)[1]), r) for t, size, r, f in spans if QTY.match(t)]
            for t, r in ids:
                q = min(qtys, key=lambda qr: abs(qr[1][0] - r[0]) + abs(qr[1][3] - r[1]), default=None)
                if q is None:
                    continue
                got = _image_above(imgs, q[1])
                if got:
                    pics[(bi, got[1])] = t
                    inventory[t] += q[0]
    ref = {k: v for k, v in ((k, looks.get(k)) for k in pics) if v is not None}
    # 2. steps and their parts boxes
    steps: list[Step] = []
    keys: list[list] = []   # per step: [(picture key, qty)]
    done = 0
    for bi, doc in enumerate(docs):
        step_size, step_font, label = _styles(doc)
        bag, pending = 0, []
        for pno in range(doc.page_count):
            done += 1
            if pno % 10 == 0:
                tick(0.6 * done / total_pages, f"booklet {bi + 1}, page {pno + 1}")
            if (bi, pno) in list_pages:
                continue
            page = doc[pno]
            spans = list(_spans(page))
            imgs = _images(page)
            big = [int(t) for t, size, r, f in spans if t.isdigit() and size >= step_size * 1.3]
            if big and pno < doc.page_count - 3:   # (the last pages point to the next booklet)
                bag = big[0]
            digits = [(t, size, r) for t, size, r, f in spans
                      if t.isdigit() and 14 <= size <= step_size + 1 and f == step_font]
            # "15" is sometimes also drawn as "1" and "5" on top of it: keep the whole number
            digits = [(t, size, r) for t, size, r in digits
                      if not any(len(t2) > len(t) and abs(s2 - size) < 1 and _overlap(r2, r) > 0.5 * _overlap(r, r)
                                 for t2, s2, r2 in digits)]
            mains = sorted((int(t), r) for t, size, r in digits if size >= step_size - 1)
            subs = [r for t, size, r in digits if size < step_size - 1]
            calls = []
            for t, size, r, f in spans:
                m = QTY.match(t)
                if m and abs(size - label) < 0.6:
                    got = _image_above(imgs, r)
                    if got:
                        calls.append(((bi, got[1]), int(m[1]), got[2]))
            here = [Step(bi, pno + 1, n, bag, [], tuple(round(v, 1) for v in r)) for n, r in mains]
            here_keys = [[] for _ in here]
            nums = [(r, i) for i, (n, r) in enumerate(mains)] + [(r, -1) for r in subs]
            for key, q, rect in calls:
                cy, cx = rect[3], rect[0]
                # a parts box sits right above its step number, which starts at the box's left edge
                below = [((r[1] - cy) + 0.2 * (cx - r[0]), i) for r, i in nums
                         if r[1] >= cy - 5 and r[0] <= cx + 5 and cx - r[0] < 260]
                if not below:
                    below = [(abs(r[1] - cy) + abs(r[0] - cx), i) for r, i in nums]
                i = min(below)[1] if below else (len(here) - 1 if here else -1)
                if i >= 0:
                    here_keys[i].append((key, q))
                else:
                    pending.append((key, q))   # a sub-build's own box: into the step it is added in
            if pending and here:
                tgt = 0
                if subs:
                    sx = min(r[0] for r in subs)
                    later = [i for i, (n, r) in enumerate(mains) if r[0] > sx]
                    tgt = later[0] if later else None
                if tgt is not None:
                    here_keys[tgt] += pending
                    pending = []
            steps += here
            keys += here_keys
        if pending and keys:
            keys[-1] += pending
    # 3. name the pictures: clearest winner first, no element used more often than listed
    calls = []
    for si, ks in enumerate(keys):
        for key, q in ks:
            lk = looks.get(key)
            if lk is None:
                continue
            best: dict[str, float] = {}
            for k, v in ref.items():
                d = _distance(lk, v)
                if d < best.get(pics[k], 9):
                    best[pics[k]] = d
            ranked = sorted((d, e) for e, d in best.items())
            if ranked:
                calls.append((si, q, ranked))
    tick(0.95, "naming the parts")
    left = Counter(inventory)
    calls.sort(key=lambda c: (c[2][1][0] - c[2][0][0]) if len(c[2]) > 1 else 9, reverse=True)
    for si, q, ranked in calls:
        d, e = next(((d, e) for d, e in ranked[:5] if left[e] >= q), ranked[0])
        left[e] -= q
        steps[si].parts.append((e, q, round(d, 4)))
    return Booklets(paths, steps, dict(inventory))


def page_picture(path: str, page: int, rect=(), width: int = PAGE_PX) -> str:
    """The booklet page a step is on, with its step number circled: a JPEG data URI ("" if the
    page can't be drawn)."""
    import base64
    import io
    from PIL import Image, ImageDraw
    try:
        doc = _fitz().open(path)
        pg = doc[page - 1]
        zoom = width / pg.rect.width
        pix = pg.get_pixmap(matrix=_fitz().Matrix(zoom, zoom), alpha=False)
    except Exception:
        return ""
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if rect:
        x0, y0, x1, y1 = (v * zoom for v in rect)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        r = max(x1 - x0, y1 - y0) * 0.5 + 0.012 * width
        d = ImageDraw.Draw(im)
        for k in range(max(3, width // 220)):   # a thick red ring around the number
            d.ellipse((cx - r - k, cy - r - k, cx + r + k, cy + r + k), outline=(210, 50, 40))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=72, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def cache_path(paths: list[str]) -> str:
    from .getbooklets import home
    h = hashlib.sha1()
    for p in sorted(paths):
        st = os.stat(p)
        h.update(f"{os.path.basename(p)}|{st.st_size}|".encode())   # LEGO booklet names are unique numbers
    return os.path.join(home(), "cache", "booklets", f"{h.hexdigest()[:16]}-v{VERSION}.json")


def load(paths: list[str], tick=lambda frac, detail="": None) -> Booklets:
    """read(), or the cached result of reading the same files before."""
    cp = cache_path(paths)
    try:
        with open(cp, encoding="utf-8") as f:
            d = json.load(f)
        return Booklets(d["files"], [Step(s["book"], s["page"], s["number"], s["bag"], [tuple(x) for x in s["parts"]],
                                          tuple(s.get("rect", ())))
                                     for s in d["steps"]], d["inventory"])
    except (OSError, ValueError, KeyError):
        pass
    b = read(paths, tick)
    try:
        os.makedirs(os.path.dirname(cp), exist_ok=True)
        with open(cp, "w", encoding="utf-8") as f:
            json.dump({"files": b.files, "inventory": b.inventory,
                       "steps": [{"book": s.book, "page": s.page, "number": s.number, "bag": s.bag, "parts": s.parts, "rect": s.rect}
                                 for s in b.steps]}, f)
    except OSError:
        pass
    return b


def find(model_path: str, sku: str = "") -> list[str]:
    """Booklet PDFs that go with a model: in Documents/bicheck/booklets/<set>, in a folder beside
    the model named with the set number, or beside the model with the set number in their name."""
    from .getbooklets import home
    here = os.path.dirname(os.path.abspath(model_path))
    found = []
    places = [os.path.join(home(), "booklets", sku)] if sku else []
    if sku:
        places += [os.path.join(here, d) for d in os.listdir(here)
                   if os.path.isdir(os.path.join(here, d)) and sku in d]
        if sku in os.path.basename(here):
            places.append(here)
    for d in places:
        if os.path.isdir(d):
            found += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(".pdf")]
    if sku:
        found += [os.path.join(here, f) for f in sorted(os.listdir(here))
                  if f.lower().endswith(".pdf") and sku in f]
    seen, out = set(), []
    for p in found:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen and is_booklet(p):
            seen.add(k)
            out.append(p)
    return out

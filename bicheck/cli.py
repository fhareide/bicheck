"""bicheck: find where an LDraw model differs from the set's building instructions.

    python -m bicheck model.mpd                          the set read from the model; its booklets found
    python -m bicheck model.mpd book1.pdf book2.pdf      the instruction booklets (PDFs from lego.com)
    python -m bicheck model.mpd https://www.lego.com/cdn/product-assets/product.bi.core.pdf/6672240.pdf ...
                                                         the booklets' links: downloaded (once) and used
    python -m bicheck model.mpd 21373 --brickset-key KEY save a Brickset API key: booklets are then
                                                         downloaded by themselves (list from Brickset)
    python -m bicheck model.mpd 21373                    set 21373's booklets, from Documents/bicheck/booklets/21373
                                                         or a folder beside the model with 21373 in its name
    python -m bicheck model.io instructions.lxfml        a BrickLink Studio model; an instructions file (LXFML)
    python -m bicheck model.mpd 21373 --find 3023 "dark bluish"   every step that uses a part

The booklets are compared step by step and part by part (their parts list gives the totals);
an LXFML instructions file also says where each part sits, so it finds parts in the wrong spot
too. The report is one HTML page: mistakes grouped by instructions step, and every part
and colour with the steps that use it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import time
import webbrowser

from . import compare, detect, getbooklets, ldraw, lxfml, mapping, progress, report

SET = re.compile(r"\d{3,7}(-\d+)?")
PAGE = "https://www.lego.com/service/building-instructions/{}"


booklet_folder = getbooklets.booklet_folder


def from_brickset(sku: str, key: str = "") -> list[str]:
    """The set's booklets downloaded with the saved Brickset key ([] without a key or a list)."""
    key = key or getbooklets.saved_key()
    if not key or not sku:
        return []
    try:
        links = getbooklets.brickset_links(key, sku)
    except Exception as e:
        print(f"  Couldn't get the booklet list from Brickset: {e}", file=sys.stderr)
        return []
    if not links:
        print(f"  Brickset has no booklets for set {sku}.", file=sys.stderr)
        return []
    print(f"Downloading the {len(links)} booklet{'s' if len(links) > 1 else ''} of set {sku} (list from Brickset)",
          file=sys.stderr)
    return getbooklets.download(links, booklet_folder(sku), log=lambda s: print("  " + s, file=sys.stderr))


def _ask(prompt: str) -> str:
    """A path or answer typed (or a file dragged) into the console window, quotes removed."""
    return input(prompt).strip().strip('"').strip("'").strip()


def _paths(text: str) -> list[str]:
    """PDFs dragged into the window (several at once: quoted, space separated), or folders of them."""
    out = []
    for x in shlex.split(text, posix=False):
        x = x.strip('"').strip("'")
        if os.path.isdir(x):
            out += [os.path.join(x, f) for f in sorted(os.listdir(x)) if f.lower().endswith(".pdf")]
        elif os.path.isfile(x) and x.lower().endswith(".pdf"):
            out.append(x)
    return out


def interactive(given: list[str]) -> int:
    """Double-clicked (or a model dropped on the program): ask for what is missing, run, open the report."""
    print("bicheck - find where an LDraw model differs from its LEGO building instructions\n")
    try:
        model = given[0] if given else ""
        while not os.path.isfile(model):
            if model:
                print(f"  Can't find {model}")
            model = _ask("Drag your model (.ldr / .mpd / .io) into this window and press Enter:\n> ")
        rest = given[1:]
        sku = next((x.partition("-")[0] for x in rest if SET.fullmatch(x)), "")
        files = [x for x in rest if os.path.exists(x)]
        if not sku:
            guesses = detect.guess(model)
            if guesses:
                g = guesses[0]
                print(f"\nThis looks like set {g} (from {g.why}).")
                if len(guesses) > 1:
                    print("  Other sets mentioned: " + ", ".join(str(x) for x in guesses[1:5]))
                sku = _ask("Press Enter to use it, or type another set number:\n> ") or g.sku
        while not SET.fullmatch(sku):
            if sku:
                print(f"  {sku} is not a set number")
            sku = _ask("\nType the set number (e.g. 21373):\n> ")
        sku = sku.partition("-")[0]
        if not files:
            from . import booklet
            files = booklet.find(model, sku)
            if files:
                print(f"\nUsing the booklets: {', '.join(os.path.basename(f) for f in files)}")
        if not files:
            files = from_brickset(sku)
        opened = False
        while not files:
            print(f"\nbicheck needs the set's instruction booklets (PDF), from {PAGE.format(sku)}")
            if not opened:
                webbrowser.open(PAGE.format(sku))
                opened = True
            print("  - paste the links of all booklets (on that page: right-click Download, Copy link), or")
            print("  - drag the downloaded PDFs into this window, or")
            print(f"  - paste your Brickset API key (free: {getbooklets.KEY_PAGE}): bicheck then downloads")
            print("    the booklets of every set by itself, now and next time.")
            got = input("Then press Enter:\n> ").strip()
            links = getbooklets.links_in(got)
            if links:
                try:
                    files = getbooklets.download(links, booklet_folder(sku), log=lambda s: print("  " + s))
                except Exception as e:
                    print(f"  Couldn't download them: {e}")
            elif _paths(got):
                files = _paths(got)
            elif re.fullmatch(r"[\w-]{8,}", got.strip('"')):
                key = got.strip('"')
                if getbooklets.check_key(key):
                    getbooklets.save_key(key)
                    print("  Key saved.")
                    files = from_brickset(sku, key)
                else:
                    print("  Brickset doesn't accept that key.")
            if not files:
                print("  No booklets yet.")
        extra = []
        if not mapping.find_library():
            lib = _ask("\nWhere is your LDraw library? Drag its folder (or complete.zip) here, "
                       "or just press Enter to go on without part names:\n> ")
            if lib and os.path.exists(lib):
                extra = ["--ldraw", lib]
        print()
        code = main([model, sku, *files, "--open", *extra])
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except Exception as e:  # show it instead of a window that closes
        import traceback
        traceback.print_exc()
        print(f"\nSomething went wrong: {e}")
        code = 1
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass
    return code


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    plain = [a for a in args if not a.startswith("-")]
    # double-clicked (no arguments), or a model dropped on the program: the window (or, with
    # --console or without Tk, questions in the console)
    if "--console" in args:
        return interactive([a for a in plain])
    if not args or (getattr(sys, "frozen", False) and len(args) == 1 and os.path.isfile(args[0])):
        try:
            from . import gui
        except ImportError:
            return interactive(plain)
        return gui.run(plain)
    ap = argparse.ArgumentParser(prog="bicheck", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", help="the model: LDraw (.mpd or .ldr) or BrickLink Studio (.io)")
    ap.add_argument("instructions", nargs="*", default=[],
                    help="the instruction booklets (PDF files or a folder of them), a set number, or an "
                         ".lxfml instructions file (default: the set the model names, its booklets found as above)")
    ap.add_argument("--brickset-key", metavar="KEY",
                    help="save your Brickset API key: missing booklets are then downloaded by themselves")
    ap.add_argument("-o", "--out", help="the HTML report (default: <model>.bicheck.html beside the model)")
    ap.add_argument("--find", nargs="+", metavar=("PART", "COLOUR"), help="list the steps that use this part (and colour)")
    ap.add_argument("--open", action="store_true", help="open the report in the browser")
    ap.add_argument("--json", action="store_true", help="print the result as JSON instead of text")
    ap.add_argument("--no-report", action="store_true", help="don't write the HTML report")
    ap.add_argument("--mapping", action="append", default=[],
                    help="an LDD-to-LDraw table (Studio's ldraw_lxfml_mapping.json, an ldraw.xml); default: the installed ones")
    ap.add_argument("--ldraw", help="the LDraw library folder or complete.zip (default: found automatically)")
    ap.add_argument("--limit", type=int, default=40, help="how many lines of each list to print (default 40)")
    a = ap.parse_args(args)

    t0 = time.time()
    if not os.path.isfile(a.model):
        ap.error(f"{a.model}: no such file")
    if a.brickset_key:
        if not getbooklets.check_key(a.brickset_key):
            ap.error("Brickset doesn't accept that API key")
        getbooklets.save_key(a.brickset_key)
        print("Brickset key saved.", file=sys.stderr)
    pdfs, lx, sku, links = [], "", "", []
    for x in a.instructions:
        if getbooklets.links_in(x):
            links += getbooklets.links_in(x)
        elif os.path.isdir(x):
            pdfs += [os.path.join(x, f) for f in sorted(os.listdir(x)) if f.lower().endswith(".pdf")]
        elif os.path.isfile(x) and x.lower().endswith(".pdf"):
            pdfs.append(x)
        elif os.path.isfile(x):
            lx = x
        elif SET.fullmatch(x):
            sku = x.partition("-")[0]
        else:
            ap.error(f"{x}: no such file or folder, and not a set number")
    if not sku and not lx:
        guesses = detect.guess(a.model)
        if guesses:
            g = guesses[0]
            others = f"; it also mentions {', '.join(str(x) for x in guesses[1:4])}" if len(guesses) > 1 else ""
            print(f"Using set {g} (found in {g.why}{others}). Give a set number to use another.", file=sys.stderr)
            sku = g.sku
        elif not pdfs and not links:
            ap.error("couldn't tell which set this model is (no set number or set name in its file name, folder, "
                     "keywords or title): give the booklets or the set number, e.g. bicheck model.mpd 21373")
    use_bi = bool(lx)
    if links:
        try:
            pdfs += getbooklets.download(links, booklet_folder(sku or "downloaded"),
                                         log=lambda s: print(s, file=sys.stderr))
        except Exception as e:
            ap.error(f"couldn't download the booklets: {e}")
    if not use_bi and not pdfs:
        from . import booklet
        pdfs = booklet.find(a.model, sku) or from_brickset(sku)
        if not pdfs:
            ap.error(f"no instruction booklets found for set {sku}. Give the booklets (PDF files or their links) "
                     f"from {PAGE.format(sku)} after the model, or save them in {booklet_folder(sku)} ; or save "
                     f"a Brickset API key (--brickset-key, free from {getbooklets.KEY_PAGE}) to have them downloaded.")
        print(f"Using the booklets {', '.join(os.path.basename(p) for p in pdfs)}", file=sys.stderr)
    if not use_bi:
        bad = [os.path.basename(p) for p in pdfs if not getbooklets.complete(p)]
        if bad:
            ap.error(f"{', '.join(bad)} {'is' if len(bad) == 1 else 'are'} incomplete or damaged (a download cut off "
                     "midway?): download again, or give the link again to have bicheck finish the download.")
    if use_bi:
        stages = ["instructions", "model", "tables", "lineup", "pairing"]
    else:
        from . import booklet
        stages = ["model", "tables"] + ([] if os.path.exists(booklet.cache_path(pdfs)) else ["booklets"]) + ["matching"]
    stages += [] if a.no_report and not a.find else ["report"]
    progress.start(stages)
    notes = []
    books = None
    try:
        if use_bi:
            progress.stage("instructions")
            bi = lxfml.load(lx)
            bi.title = os.path.basename(lx)
        progress.stage("model")
        model = ldraw.read(a.model)
        if not model.parts:
            progress.finish()
            ap.error(f"{os.path.basename(a.model)} has no parts in it (only {len(open(a.model, 'rb').read().splitlines())} "
                     "lines, none a part): save the model again from your LDraw editor, or pick the right file.")
        progress.stage("tables")
        m = mapping.load(a.mapping or None, a.ldraw)
        if not m.sources:
            notes.append("Note: no LDD-to-LDraw table found (install BrickLink Studio, or pass --mapping): parts are "
                         "matched by number only" + (" and can't be lined up by position." if use_bi else "."))
        if not m.color_names:
            notes.append("Note: no LDraw library found (pass --ldraw): colours are LEGO ids, no part names.")
        if use_bi:
            r = compare.compare(bi, model, m)
        else:
            from . import booklet, stepmatch
            if "booklets" in stages:
                progress.stage("booklets")
            try:
                books = booklet.load(pdfs, tick=progress.tick)
            except ImportError:
                progress.finish()
                ap.error("reading PDF booklets needs PyMuPDF: pip install pymupdf")
            if not books.inventory:
                notes.append("Note: no parts list found in the booklets (it is on the last pages of the last "
                             "booklet): give every booklet of the set.")
            progress.stage("matching")
            name = detect.sets().get(sku, "") if sku else ""
            title = f"LEGO {sku} {name}".strip() if sku else "the instruction booklets"
            bi, unknown = stepmatch.make_bi(books, model, m, title)
            r = stepmatch.match(bi, model, m)
            for e, n in unknown.items():
                r.skipped[f"element {e}: not in Rebrickable's element table"] += n
        if "report" in stages:
            progress.stage("report")
        d = report.data(r)
        d["summary"]["seconds"] = round(time.time() - t0, 1)
        if books is not None:
            d["summary"]["mode"] = "booklets"
            d["summary"]["booklets"] = [os.path.basename(p) for p in books.files]
            # each step with a difference: its booklet page, the step number circled
            pages = {}
            for k in sorted({x["step"] for x in d["issues"] if x.get("step")}):
                if 1 <= k <= len(books.steps):
                    st = books.steps[k - 1]
                    pages[str(k)] = booklet.page_picture(books.files[st.book], st.page, st.rect)
            d["pages"] = {k: v for k, v in pages.items() if v}
        out = None
        if not a.no_report and not a.find:
            out = a.out or os.path.splitext(os.path.abspath(a.model))[0] + ".bicheck.html"
            report.write_html(d, out, m.lib)
    finally:
        progress.finish()
    for n in notes:
        print(n, file=sys.stderr)

    if a.find:
        print(report.find(d, a.find[0], " ".join(a.find[1:])))
        return 0
    if a.json:
        print(json.dumps(d, indent=1))
    else:
        print(report.text(d, a.limit))
    if out:
        print(f"\nReport: {out}", file=sys.stderr)
        if a.open:
            webbrowser.open("file:///" + out.replace(os.sep, "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

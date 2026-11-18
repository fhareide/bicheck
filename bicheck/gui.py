"""A small window for bicheck: pick the model, check the set and its booklets, press Check.

Opens when the program is double-clicked (or a model is dropped on it). The work runs in a
thread through cli.main, so the window and the command line give the same results; the
thread talks to the window only through a queue the window polls.
"""
from __future__ import annotations

import contextlib
import io
import os
import queue
import re
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from . import __version__, cli, detect, getbooklets, mapping, progress

PAD = 12


class App:
    def __init__(self, root: tk.Tk, model: str = ""):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.files: list[str] = []
        self.report = ""
        self.auto_set = ""
        root.title(f"bicheck {__version__}")
        root.minsize(560, 0)

        f = ttk.Frame(root, padding=PAD)
        f.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Find where your LDraw model differs from the LEGO building instructions.",
                  foreground="#555").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, PAD))

        # the model
        ttk.Label(f, text="Model").grid(row=1, column=0, sticky="w")
        self.model = tk.StringVar(value=model)
        e = ttk.Entry(f, textvariable=self.model)
        e.grid(row=1, column=1, sticky="ew", padx=6)
        e.bind("<FocusOut>", lambda _: self.model_changed())
        e.bind("<Return>", lambda _: self.model_changed())
        ttk.Button(f, text="Browse…", command=self.pick_model).grid(row=1, column=2, sticky="ew")

        # the set
        ttk.Label(f, text="Set").grid(row=2, column=0, sticky="w", pady=(8, 0))
        sf = ttk.Frame(f)
        sf.grid(row=2, column=1, columnspan=2, sticky="ew", padx=6, pady=(8, 0))
        self.sku = tk.StringVar()
        se = ttk.Entry(sf, textvariable=self.sku, width=10)
        se.pack(side="left")
        se.bind("<FocusOut>", lambda _: self.set_changed())
        se.bind("<Return>", lambda _: self.set_changed())
        self.set_name = ttk.Label(sf, text="", foreground="#555")
        self.set_name.pack(side="left", padx=8)

        # the booklets
        bf = ttk.LabelFrame(f, text="Instruction booklets (PDF)", padding=8)
        bf.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(PAD, 0))
        bf.columnconfigure(0, weight=1)
        f.rowconfigure(3, weight=1)
        self.books = tk.Listbox(bf, height=4, activestyle="none")
        self.books.grid(row=0, column=0, rowspan=4, sticky="nsew")
        bf.rowconfigure(3, weight=1)
        ttk.Button(bf, text="Add PDFs…", command=self.add_pdfs).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Button(bf, text="Paste links…", command=self.paste_links).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=4)
        ttk.Button(bf, text="Open LEGO page", command=self.open_page).grid(row=2, column=1, sticky="ew", padx=(8, 0))
        self.key_btn = ttk.Button(bf, text="Brickset key…", command=self.brickset_key)
        self.key_btn.grid(row=3, column=1, sticky="new", padx=(8, 0), pady=(4, 0))
        self.book_note = ttk.Label(bf, text="", foreground="#555", wraplength=520, justify="left")
        self.book_note.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # the LDraw library, only asked for when bicheck can't find one
        self.ldraw = tk.StringVar()
        row = 4
        if not mapping.find_library():
            ttk.Label(f, text="LDraw library").grid(row=row, column=0, sticky="w", pady=(PAD, 0))
            ttk.Entry(f, textvariable=self.ldraw).grid(row=row, column=1, sticky="ew", padx=6, pady=(PAD, 0))
            ttk.Button(f, text="Browse…", command=lambda: self.ldraw.set(filedialog.askdirectory(
                title="The LDraw library folder") or self.ldraw.get())).grid(row=row, column=2, sticky="ew", pady=(PAD, 0))
            row += 1

        # run
        rf = ttk.Frame(f)
        rf.grid(row=row + 1, column=0, columnspan=3, sticky="ew", pady=(PAD, 0))
        rf.columnconfigure(0, weight=1)
        self.bar = ttk.Progressbar(rf, maximum=1000)
        self.bar.grid(row=0, column=0, sticky="ew")
        self.run_btn = ttk.Button(rf, text="Check model", command=self.run)
        self.run_btn.grid(row=0, column=1, padx=(8, 0))
        self.status = ttk.Label(f, text="", wraplength=540, justify="left")
        self.status.grid(row=row + 2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.open_btn = ttk.Button(f, text="Open the report", command=self.open_report)
        self.open_btn.grid(row=row + 3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.open_btn.grid_remove()

        self.update_key_button()
        progress.listener = lambda p, text: self.q.put(("bar", p, text))
        root.after(100, self.poll)
        if model:
            root.after(50, self.model_changed)

    # -- the thread talks to the window only through the queue ------------------------------
    def poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                getattr(self, "on_" + msg[0])(*msg[1:])
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def work(self, fn, *args):
        def go():
            try:
                fn(*args)
            except Exception as e:  # shown in the window, not lost
                self.q.put(("error", str(e)))
            finally:
                self.q.put(("idle",))
        self.busy = True
        self.run_btn.state(["disabled"])
        threading.Thread(target=go, daemon=True).start()

    def on_bar(self, p, text):
        self.bar["value"] = int(p * 1000)
        self.say(text)

    def on_say(self, text, colour=""):
        self.say(text, colour)

    def on_error(self, text):
        self.bar["value"] = 0
        self.say(text, "#b3261e")

    def on_idle(self):
        self.busy = False
        self.run_btn.state(["!disabled"])

    def on_books(self, files, note):
        self.files = files
        self.books.delete(0, "end")
        for p in files:
            self.books.insert("end", os.path.basename(p))
        self.book_note.configure(text=note)

    def on_key_saved(self):
        self.update_key_button()
        self.say("Key saved.")
        self.find_books()

    def on_done(self, out, summary):
        self.report = out
        self.say(summary)
        self.open_btn.grid()

    def say(self, text, colour=""):
        self.status.configure(text=text, foreground=colour or "")

    # -- the model, the set, the booklets ----------------------------------------------------
    def pick_model(self):
        p = filedialog.askopenfilename(title="Your model", filetypes=[
            ("LDraw or Studio model", "*.ldr *.mpd *.io *.dat"), ("All files", "*.*")])
        if p:
            self.model.set(os.path.normpath(p))
            self.model_changed()

    def model_changed(self):
        m = self.model.get().strip().strip('"')
        if not os.path.isfile(m):
            return
        if not self.sku.get().strip() or self.sku.get().strip() == self.auto_set:
            g = detect.guess(m)
            if g:
                self.sku.set(g[0].sku)
                self.auto_set = g[0].sku
        self.set_changed()

    def set_changed(self):
        sku = self.sku.get().strip().partition("-")[0]
        self.set_name.configure(text=detect.sets().get(sku, "") if sku else "")
        self.find_books()

    def find_books(self):
        m, sku = self.model.get().strip().strip('"'), self.sku.get().strip().partition("-")[0]
        if not (os.path.isfile(m) and cli.SET.fullmatch(sku or "")):
            return

        def go():
            from . import booklet
            files = booklet.find(m, sku)
            if files:
                note = f"Found in {os.path.dirname(files[0])}"
            elif getbooklets.saved_key():
                note = "Not here yet: they are downloaded (list from Brickset) when you press Check model."
            else:
                note = ("None yet. Open the LEGO page and add the downloaded PDFs, or paste their links "
                        "(right-click Download, Copy link), or save a Brickset key to have them downloaded.")
            self.q.put(("books", files, note))
        threading.Thread(target=go, daemon=True).start()

    def add_pdfs(self):
        ps = filedialog.askopenfilenames(title="The set's instruction booklets", filetypes=[("PDF", "*.pdf")])
        if ps:
            files = list(dict.fromkeys(self.files + [os.path.normpath(p) for p in ps]))
            self.on_books(files, f"{len(files)} booklet{'s' if len(files) > 1 else ''} chosen")

    def open_page(self):
        sku = self.sku.get().strip().partition("-")[0]
        webbrowser.open(cli.PAGE.format(sku) if sku else "https://www.lego.com/service/building-instructions")

    def paste_links(self):
        sku = self.sku.get().strip().partition("-")[0]
        if not cli.SET.fullmatch(sku or ""):
            messagebox.showinfo("bicheck", "Type the set number first.")
            return
        w = tk.Toplevel(self.root)
        w.title("Paste the booklets' links")
        w.transient(self.root)
        ttk.Label(w, padding=(PAD, PAD, PAD, 4), wraplength=460, justify="left", text=(
            f"On the LEGO page of set {sku}, right-click each booklet's Download button, choose Copy link, "
            "and paste them all here:")).pack(fill="x")
        t = tk.Text(w, width=64, height=6)
        t.pack(fill="both", expand=True, padx=PAD)
        try:
            clip = self.root.clipboard_get()
            if getbooklets.links_in(clip):
                t.insert("1.0", clip)
        except tk.TclError:
            pass

        def ok():
            links = getbooklets.links_in(t.get("1.0", "end"))
            if not links:
                messagebox.showinfo("bicheck", "No PDF links there.", parent=w)
                return
            w.destroy()
            self.work(self.download, links, sku)
        bb = ttk.Frame(w, padding=PAD)
        bb.pack(fill="x")
        ttk.Button(bb, text="Download", command=ok).pack(side="right")
        ttk.Button(bb, text="Cancel", command=w.destroy).pack(side="right", padx=6)
        t.focus_set()

    def download(self, links, sku):
        n = len(links)
        self.q.put(("say", f"Downloading {n} booklet{'s' if n > 1 else ''}…"))
        files = getbooklets.download(links, cli.booklet_folder(sku),
                                     tick=lambda p: self.q.put(("bar", p, "Downloading the booklets")))
        self.q.put(("books", files, f"Downloaded to {cli.booklet_folder(sku)}"))
        self.q.put(("say", "Booklets ready: press Check model."))

    def update_key_button(self):
        self.key_btn.configure(text="Brickset key ✓" if getbooklets.saved_key() else "Brickset key…")

    def brickset_key(self):
        w = tk.Toplevel(self.root)
        w.title("Brickset API key")
        w.transient(self.root)
        ttk.Label(w, padding=(PAD, PAD, PAD, 4), wraplength=440, justify="left", text=(
            "With your own free Brickset API key, bicheck gets each set's list of booklets from Brickset "
            "and downloads them by itself. Only the set number and the key are sent.")).pack(fill="x")
        link = ttk.Label(w, text=getbooklets.KEY_PAGE, foreground="#1f5fbf", cursor="hand2", padding=(PAD, 0))
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda _: webbrowser.open(getbooklets.KEY_PAGE))
        v = tk.StringVar(value=getbooklets.saved_key())
        ttk.Entry(w, textvariable=v, width=48).pack(fill="x", padx=PAD, pady=8)

        def ok():
            key = v.get().strip()
            if not key:
                return
            w.destroy()
            self.work(self.save_key, key)
        bb = ttk.Frame(w, padding=(PAD, 0, PAD, PAD))
        bb.pack(fill="x")
        ttk.Button(bb, text="Save", command=ok).pack(side="right")
        ttk.Button(bb, text="Cancel", command=w.destroy).pack(side="right", padx=6)

    def save_key(self, key):
        self.q.put(("say", "Checking the key with Brickset…"))
        if not getbooklets.check_key(key):
            self.q.put(("error", "Brickset doesn't accept that key."))
            return
        getbooklets.save_key(key)
        self.q.put(("key_saved",))

    # -- checking -----------------------------------------------------------------------------
    def run(self):
        if self.busy:
            return
        m, sku = self.model.get().strip().strip('"'), self.sku.get().strip().partition("-")[0]
        if not os.path.isfile(m):
            self.on_error("Choose your model first.")
            return
        if not cli.SET.fullmatch(sku or ""):
            self.on_error("Type the set number (for example 21373).")
            return
        self.open_btn.grid_remove()
        self.bar["value"] = 0
        args = [m, sku, *self.files, "--open"]
        if self.ldraw.get().strip():
            args += ["--ldraw", self.ldraw.get().strip()]
        self.work(self.check, args)

    def check(self, args):
        out_text, err_text = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out_text), contextlib.redirect_stderr(err_text):
            try:
                code = cli.main(args)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        err = err_text.getvalue()
        if code:
            m = re.findall(r"bicheck: error: (.*)", err)
            self.q.put(("error", m[-1] if m else (err.strip().splitlines() or ["Something went wrong."])[-1]))
            return
        rep = re.findall(r"Report: (.*)", err)
        lines = out_text.getvalue().splitlines()
        summary = "\n".join(x for x in lines[:4] if x.startswith(("Found", "Compared")) or "parts;" in x)
        notes = [x for x in err.splitlines() if x.startswith("Note:")]
        self.q.put(("done", rep[-1].strip() if rep else "", "\n".join([summary] + notes)))

    def open_report(self):
        if self.report:
            webbrowser.open("file:///" + self.report.replace(os.sep, "/"))


def _hide_console():
    """Double-clicked, the exe gets a console window of its own: hide it behind the window
    (left alone when started from a console someone typed in)."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        procs = (ctypes.c_uint * 8)()
        if k.GetConsoleProcessList(procs, 8) <= 2:   # this program (and its launcher) only
            ctypes.windll.user32.ShowWindow(k.GetConsoleWindow(), 0)
    except Exception:
        pass


def sharp() -> None:
    """Draw at the screen's real resolution (otherwise Windows stretches the window and the text
    goes soft on a scaled screen)."""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def run(given: list[str]) -> int:
    sharp()
    root = tk.Tk()
    _hide_console()
    try:
        ttk.Style().theme_use("vista" if os.name == "nt" else "clam")
    except tk.TclError:
        pass
    model = next((g for g in given if os.path.isfile(g)), "")
    App(root, model)
    root.mainloop()
    progress.listener = None
    return 0

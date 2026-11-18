"""One progress bar for the whole run, on stderr.

    progress.start(["download", "instructions", ...])   the stages this run will go through
    progress.stage("instructions")                        a stage begins (the bar jumps to its start)
    progress.tick(0.4)                                    40 % through the current stage
    progress.finish()

Each stage has a share of the bar (WEIGHTS, roughly its time on a big set). In a console the
bar redraws in place; when stderr is a file or a pipe each stage is one line instead, so
logs stay readable. Nothing is drawn until start() is called (tests, library use).
"""
from __future__ import annotations

import sys
import time

WEIGHTS = {
    "download": 25, "instructions": 10, "model": 5, "tables": 15,   # measured on Downton Abbey (4,700 parts)
    "lineup": 60, "pairing": 3, "report": 15,
    "booklets": 50, "matching": 5,
}
LABELS = {
    "download": "Downloading the instructions",
    "instructions": "Reading the instructions",
    "model": "Reading your model",
    "tables": "Loading the LEGO-to-LDraw part tables",
    "lineup": "Lining your model up with the instructions",
    "pairing": "Comparing part by part",
    "report": "Drawing the parts and writing the report",
    "booklets": "Reading the booklets (once per set)",
    "matching": "Comparing step by step",
}
WIDTH = 30
listener = None   # a window's progress bar: called with (fraction 0..1, text), from the working thread

_state: dict = {}


def start(stages: list[str], stream=None) -> None:
    total = sum(WEIGHTS.get(s, 5) for s in stages) or 1
    _state.clear()
    _state.update(stages=stages, total=total, stream=stream or sys.stderr, cur=None, frac=0.0,
                  done=0, last=0.0, t0=time.time())
    _state["tty"] = bool(getattr(_state["stream"], "isatty", lambda: False)())


def active() -> bool:
    return bool(_state)


def stage(name: str, label: str | None = None) -> None:
    if not _state:
        return
    if _state["cur"] is not None:
        _state["done"] += WEIGHTS.get(_state["cur"], 5)
    _state.update(cur=name, frac=0.0, label=label or LABELS.get(name, name))
    if not _state["tty"]:
        print(f"{_state['label']}...", file=_state["stream"], flush=True)
    _draw(force=True)


def tick(frac: float, detail: str = "") -> None:
    if not _state or _state["cur"] is None:
        return
    _state["frac"] = max(0.0, min(1.0, frac))
    _state["detail"] = detail
    _draw()


def _draw(force: bool = False) -> None:
    if not _state["tty"] and listener is None:
        return
    now = time.time()
    if not force and now - _state["last"] < 0.08:  # don't flood the console
        return
    _state["last"] = now
    w = _state["done"] + WEIGHTS.get(_state["cur"], 5) * _state["frac"]
    p = min(1.0, w / _state["total"])
    if listener is not None:
        listener(p, _state["label"] + (f" ({_state['detail']})" if _state.get("detail") else ""))
    if not _state["tty"]:
        return
    n = int(round(p * WIDTH))
    text = f"\r[{'#' * n}{'-' * (WIDTH - n)}] {int(p * 100):3d}%  {_state['label']}"
    if _state.get("detail"):
        text += f" ({_state['detail']})"
    print(text[:110].ljust(110), end="", file=_state["stream"], flush=True)


def finish() -> None:
    if not _state:
        return
    secs = time.time() - _state["t0"]
    if listener is not None:
        listener(1.0, f"Done in {secs:.1f} s")
    if _state["tty"]:
        print(f"\r[{'#' * WIDTH}] 100%  Done in {secs:.1f} s".ljust(110), file=_state["stream"], flush=True)
    else:
        print(f"Done in {secs:.1f} s", file=_state["stream"], flush=True)
    _state.clear()

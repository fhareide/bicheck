"""Download a set's instruction booklets (PDF) into Documents/bicheck/booklets/<set>.

lego.com doesn't let programs read which booklets belong to a set, but the PDFs themselves
download fine. The list comes from either
  - links the user pastes (right-click a booklet's Download button on lego.com, Copy link), or
  - Brickset's API (getInstructions2), with the user's own free key from
    https://brickset.com/tools/webservices/requestkey, saved once in settings.json.
Only the set number and the key are sent to Brickset.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

from . import __version__

API = "https://brickset.com/api/v3.asmx/"
KEY_PAGE = "https://brickset.com/tools/webservices/requestkey"
UA = {"User-Agent": f"bicheck/{__version__}"}
URL = re.compile(r"https?://\S+", re.I)


def home() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "bicheck")


def booklet_folder(sku: str) -> str:
    return os.path.join(home(), "booklets", sku)


# -- the saved key -----------------------------------------------------------------------------
def _settings_path() -> str:
    return os.path.join(home(), "settings.json")


def _settings() -> dict:
    try:
        with open(_settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def saved_key() -> str:
    return os.environ.get("BRICKSET_KEY", "").strip() or str(_settings().get("brickset_key", "")).strip()


def save_key(key: str) -> None:
    s = _settings()
    s["brickset_key"] = key.strip()
    os.makedirs(home(), exist_ok=True)
    with open(_settings_path(), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)


# -- Brickset ----------------------------------------------------------------------------------
def _call(method: str, **params) -> dict:
    url = API + method + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def check_key(key: str) -> bool:
    try:
        return _call("checkKey", apiKey=key).get("status") == "success"
    except Exception:
        return False


def _lego(url: str) -> bool:
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and (host == "lego.com" or host.endswith(".lego.com")) and u.path.lower().endswith(".pdf")


def pick(items: list[dict]) -> list[str]:
    """The booklet links of one printing: a set can be listed with several printings of the same
    booklets ("V29", "V39"); the one with the most booklets (the first listed on a tie) is kept."""
    groups: dict[str, list[str]] = {}
    for it in items:
        url = str(it.get("URL") or it.get("url") or "").strip()
        if not _lego(url):
            continue
        m = re.search(r"\bV\s?(\d{2})\b", str(it.get("description") or ""))
        groups.setdefault(m[1] if m else "", []).append(url)
    if not groups:
        return []
    best = max(groups.values(), key=len)
    return list(dict.fromkeys(best))


def brickset_links(key: str, sku: str) -> list[str]:
    """The set's booklet links from Brickset; [] if it lists none. Raises on a bad key."""
    for n in (f"{sku}-1", sku):
        d = _call("getInstructions2", apiKey=key, setNumber=n)
        if d.get("status") != "success":
            raise RuntimeError(f"Brickset: {d.get('message') or 'error'}")
        links = pick(d.get("instructions") or [])
        if links:
            return links
    return []


# -- downloading -------------------------------------------------------------------------------
def links_in(text: str) -> list[str]:
    """PDF links pasted or given on the command line (quotes and trailing punctuation removed)."""
    out = []
    for u in URL.findall(text or ""):
        u = u.strip("\"'<>(),;")
        if urllib.parse.urlparse(u).path.lower().endswith(".pdf"):
            out.append(u)
    return list(dict.fromkeys(out))


def complete(path: str) -> bool:
    """A whole PDF: it starts with %PDF- and ends with %%EOF (a download cut off midway doesn't,
    and then has no page index: it opens with no pages)."""
    try:
        with open(path, "rb") as f:
            if f.read(5) != b"%PDF-":
                return False
            f.seek(max(0, os.path.getsize(path) - 2048))
            return b"%%EOF" in f.read()
    except OSError:
        return False


def _fetch(url: str, part: str, report=None) -> None:
    """The file at `url` into `part`, picking up where a cut-off download stopped (a big
    booklet's connection can close early without an error)."""
    size = 0
    for attempt in range(6):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if size and have >= size:
            return
        headers = dict(UA, **({"Range": f"bytes={have}-"} if have else {}))
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
                if have and r.status != 206:   # the server sent it all again
                    have = 0
                n = int(r.headers.get("Content-Length") or 0)
                if n:
                    size = have + n
                with open(part, "ab" if have else "wb") as f:
                    got = have
                    while True:
                        b = r.read(1 << 16)
                        if not b:
                            break
                        f.write(b)
                        got += len(b)
                        if report and size:
                            report(got / size)
        except OSError:
            if attempt == 5:
                raise
        if size and os.path.getsize(part) >= size:
            return
        if not size and complete(part):
            return
    raise OSError(f"the download kept stopping early ({os.path.getsize(part) // 1_000_000} of "
                  f"{size // 1_000_000 or '?'} MB)")


def download(urls: list[str], folder: str, tick=None, log=None) -> list[str]:
    """Each PDF into `folder` (kept when already there and whole); the files' paths. Anything that
    isn't a PDF is dropped."""
    os.makedirs(folder, exist_ok=True)
    out = []
    for i, url in enumerate(urls):
        name = os.path.basename(urllib.parse.urlparse(url).path) or f"booklet{i + 1}.pdf"
        name = re.sub(r"[^\w.\-]", "_", name)
        path = os.path.join(folder, name)
        part = path + ".part"
        if os.path.exists(path) and not complete(path):
            os.replace(path, part)   # cut off last time: pick up where it stopped
        if not os.path.exists(path):
            if log:
                log(f"Downloading {name}")
            shown = [-1]

            def report(frac):
                if tick:
                    tick((i + frac) / len(urls))
                pct = int(frac * 100) // 10 * 10
                if log and pct > shown[0]:
                    shown[0] = pct
                    print(f"\r    {pct}%", end="", file=sys.stderr, flush=True)
            _fetch(url, part, report)
            if log:
                print("\r" + " " * 12 + "\r", end="", file=sys.stderr, flush=True)
            with open(part, "rb") as f:
                ok = f.read(5) == b"%PDF-"
            if not ok:
                os.remove(part)
                if log:
                    log(f"  {url} is not a PDF, skipped")
                continue
            if not complete(part):
                raise OSError(f"{name} arrived incomplete; try again")
            os.replace(part, path)
        out.append(path)
        if tick:
            tick((i + 1) / len(urls))
    return out

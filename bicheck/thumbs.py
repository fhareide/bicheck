"""Small pictures of parts in a colour, drawn from the LDraw library: the report's thumbnails.

Each part file is read down to its triangles, quads and edge lines (sub-parts and primitives
included), turned to the usual three-quarter view, shaded by a light from the upper left and
drawn back to front at three times the size, then scaled down. No network, no 3D library.
"""
from __future__ import annotations

import base64
import io
import re
from functools import lru_cache

import numpy as np

SIZE = 128          # px, the thumbnail (shown at 68 px: sharp on high-DPI screens too)
EDGE_PX = 1.1       # edge line width in thumbnail px
SUPER = 2           # drawn this many times larger, then scaled down (smooth edges)
EDGE_OF = 10000     # colour code EDGE_OF + c: the edge colour of colour c
_COLOUR = re.compile(r"0\s+!COLOUR\s+\S+\s+CODE\s+(\d+)\s+VALUE\s+#([0-9A-Fa-f]{6})\s+EDGE\s+#([0-9A-Fa-f]{6})(.*)")


def _view() -> np.ndarray:
    """LDraw axes (x right, y down, z toward the viewer) -> the screen, seen from front-right-above."""
    yaw, pitch = np.radians(-45), np.radians(-28)
    ry = np.array([[np.cos(yaw), 0, np.sin(yaw)], [0, 1, 0], [-np.sin(yaw), 0, np.cos(yaw)]])
    rx = np.array([[1, 0, 0], [0, np.cos(pitch), -np.sin(pitch)], [0, np.sin(pitch), np.cos(pitch)]])
    return rx @ ry


VIEW = _view()


def _split(P: np.ndarray, C: np.ndarray, most: float) -> tuple[np.ndarray, np.ndarray]:
    """Triangles (n,3,3) or quads (n,4,3), with their colours, cut into four until no side is
    longer than `most` (at most four times)."""
    for _ in range(4):
        side = np.linalg.norm(P - np.roll(P, 1, axis=1), axis=2).max(1)
        big = side > most
        if not big.any():
            break
        Q, keep = P[big], P[~big]
        if P.shape[1] == 3:
            a, b, c = Q[:, 0], Q[:, 1], Q[:, 2]
            ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
            kids = [np.stack(x, 1) for x in ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))]
        else:
            a, b, c, d = Q[:, 0], Q[:, 1], Q[:, 2], Q[:, 3]
            ab, bc, cd, da, m = (a + b) / 2, (b + c) / 2, (c + d) / 2, (d + a) / 2, Q.mean(1)
            kids = [np.stack(x, 1) for x in ((a, ab, m, da), (ab, b, bc, m), (m, bc, c, cd), (da, m, cd, d))]
        P = np.concatenate([keep] + kids)
        C = np.concatenate([C[~big]] + [C[big]] * 4)
    return P, C


class Renderer:
    def __init__(self, lib):
        self.lib = lib
        self.colors: dict[int, tuple] = {}   # code -> (rgb, edge rgb, alpha)
        text = lib.read("LDConfig.ldr") or ""
        for line in text.splitlines():
            m = _COLOUR.match(line.strip())
            if m:
                rgb = tuple(int(m[2][i:i + 2], 16) for i in (0, 2, 4))
                edge = tuple(int(m[3][i:i + 2], 16) for i in (0, 2, 4))
                a = re.search(r"ALPHA\s+(\d+)", m[4])
                self.colors[int(m[1])] = (rgb, edge, int(a[1]) if a else 255)
        self._geo = lru_cache(maxsize=4096)(self._load)
        self._drawn_cache: dict = {}

    # -- geometry ------------------------------------------------------------------------
    def _text(self, name: str) -> str | None:
        name = name.replace("\\", "/").strip()
        for root in ("parts/", "p/", ""):
            t = self.lib.read(root + name)
            if t is not None:
                return t
        return None

    def _load(self, name: str, depth: int = 0):
        """(polys: list of (points (k,3), colour)), lines: (n,2,3), line colours) in the file's frame."""
        text = self._text(name) if depth < 12 else None
        polys_v, polys_c, lines_v, lines_c = [], [], [], []
        if text is None:
            return [], np.zeros((0, 2, 3)), np.zeros(0, int)
        for line in text.splitlines():
            f = line.split()
            if not f or f[0] not in "1234" or len(f[0]) != 1:
                continue
            try:
                c = int(f[1])
            except ValueError:
                c = 16  # direct colours (0x2RRGGBB): drawn in the part's colour
            if f[0] == "1" and len(f) >= 15:
                v = [float(x) for x in f[2:14]]
                M, p = np.array(v[3:]).reshape(3, 3), np.array(v[:3])
                polys, lv, lc = self._geo(" ".join(f[14:]).lower(), depth + 1)
                for pts, pc in polys:
                    polys_v.append(pts @ M.T + p)
                    polys_c.append(self._inherit(pc, c))
                if len(lv):
                    lines_v.append(lv @ M.T + p)
                    lines_c.append(np.array([self._inherit(int(x), c) for x in lc]))
            elif f[0] == "2" and len(f) >= 8:
                lines_v.append(np.array([float(x) for x in f[2:8]]).reshape(1, 2, 3))
                lines_c.append(np.array([c]))
            elif f[0] == "3" and len(f) >= 11:
                polys_v.append(np.array([float(x) for x in f[2:11]]).reshape(3, 3))
                polys_c.append(c)
            elif f[0] == "4" and len(f) >= 14:
                polys_v.append(np.array([float(x) for x in f[2:14]]).reshape(4, 3))
                polys_c.append(c)
        lv = np.concatenate(lines_v) if lines_v else np.zeros((0, 2, 3))
        lc = np.concatenate(lines_c) if lines_c else np.zeros(0, int)
        return list(zip(polys_v, polys_c)), lv, lc

    @staticmethod
    def _inherit(child: int, parent: int) -> int:
        if child == 16:
            return parent
        if child == 24:
            return 24 if parent == 16 else EDGE_OF + parent
        return child

    # -- drawing -------------------------------------------------------------------------
    def _rgb(self, code: int, main: int) -> tuple:
        if code == 16:
            code = main
        if code == 24:
            code = EDGE_OF + main
        if code >= EDGE_OF:
            base = self.colors.get(code - EDGE_OF)
            if base is None:
                return (40, 40, 40, 255)
            rgb, edge, _ = base
            if sum(rgb) < 150:  # LDraw draws black parts with grey edges; a little softer here
                edge = (70, 70, 70)
            return (*edge, 255)
        rgb, _, a = self.colors.get(code, ((160, 160, 160), (60, 60, 60), 255))
        return (*rgb, max(a, 140))

    def _drawn(self, file: str):
        """The part drawn once for every colour: per pixel its shade (0..255) and which colour it
        takes (0 = the part's colour, 1 = its edges, 2.. = a fixed colour in `codes`); None if
        the library hasn't the part."""
        if file in self._drawn_cache:
            return self._drawn_cache[file]
        from PIL import Image
        polys, lv, lc = self._geo(file.lower())
        if not polys:
            self._drawn_cache[file] = None
            return None
        big = SIZE * SUPER
        codes: list[int] = []
        def role(code: int) -> int:
            if code == 16:
                return 0
            if code == 24:
                return 1
            if code not in codes:
                codes.append(code)
            return 2 + codes.index(code)
        light = np.array([-0.45, -0.75, 0.5])
        light /= np.linalg.norm(light)
        # quads as two triangles; everything turned to the view (z toward the viewer)
        tris, tc = [], []
        for p, c in polys:
            r = role(c)
            tris.append(p[:3]); tc.append(r)
            if len(p) == 4:
                tris.append(p[[0, 2, 3]]); tc.append(r)
        T = np.stack(tris) @ VIEW.T
        TC = np.array(tc)
        L = lv @ VIEW.T if len(lv) else np.zeros((0, 2, 3))
        allp = np.concatenate([T.reshape(-1, 3), L.reshape(-1, 3)])
        lo, hi = allp[:, :2].min(0), allp[:, :2].max(0)
        scale = (big * 0.9) / max(max(hi - lo), 1e-6)
        off = (big - (hi - lo) * scale) / 2 - lo * scale
        # Faces painted back to front by their middles (large faces cut small first, so a plate's
        # top doesn't cover studs behind its middle), each pixel keeping which face it shows. The
        # depth at each pixel then comes from that face's plane, and edge lines are drawn only
        # where they aren't behind the face there: no hidden edges showing through the sides.
        size = max(np.ptp(T.reshape(-1, 3), axis=0).max(), 1.0)
        T, TC = _split(T, TC, size / 6)
        n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
        nn = np.linalg.norm(n, axis=1)
        ok = nn > 1e-9
        n[ok] /= nn[ok, None]
        n *= np.where(n[:, 2:3] < 0, -1, 1)  # face the viewer (both sides drawn)
        shades = (0.62 + 0.48 * np.clip(n @ light, 0, None)).astype(np.float32)
        XY = T[:, :, :2] * scale + off
        Z = T[:, :, 2]
        from PIL import ImageDraw
        ids = Image.new("I", (big, big), 0)
        d = ImageDraw.Draw(ids)
        flat = XY.reshape(len(XY), -1).tolist()
        for i in np.argsort(Z.mean(1)).tolist():   # far (small z) first
            d.polygon(flat[i], fill=i + 1, outline=i + 1)
        face = np.asarray(ids).astype(np.int32) - 1
        covered = face >= 0
        cy, cx = np.nonzero(covered)
        f = face[cy, cx]
        # each face's plane in pixel space: z = a x + b y + c (only for the faces that show)
        x0, y0 = XY[:, 0, 0], XY[:, 0, 1]
        ex1, ey1, ez1 = XY[:, 1, 0] - x0, XY[:, 1, 1] - y0, Z[:, 1] - Z[:, 0]
        ex2, ey2, ez2 = XY[:, 2, 0] - x0, XY[:, 2, 1] - y0, Z[:, 2] - Z[:, 0]
        det = ex1 * ey2 - ex2 * ey1
        flat_on = np.abs(det) < 1e-6
        det = np.where(flat_on, 1.0, det)
        a = np.where(flat_on, 0.0, (ez1 * ey2 - ez2 * ey1) / det)
        b = np.where(flat_on, 0.0, (ex1 * ez2 - ex2 * ez1) / det)
        c = np.where(flat_on, Z.mean(1), Z[:, 0] - a * x0 - b * y0)
        zbuf = np.full((big, big), -np.inf, np.float32)
        zbuf[cy, cx] = np.clip(a[f] * (cx + 0.5) + b[f] * (cy + 0.5) + c[f], Z.min(1)[f], Z.max(1)[f])
        shade_b = np.zeros((big, big), np.float32)
        shade_b[cy, cx] = shades[f]
        role_b = np.zeros((big, big), np.int32)
        role_b[cy, cx] = TC[f]
        # edge lines: drawn where they are not behind a face (a little slack for the faces they lie on)
        if len(L):
            lw = max(1, round(EDGE_PX * SUPER))
            slack = 1.5   # LDraw units
            # every line sampled every half pixel, all lines at once
            P0, P1 = L[:, 0, :2] * scale + off, L[:, 1, :2] * scale + off
            n = (np.abs(P1 - P0).max(1) * 2).astype(int) + 2
            line = np.repeat(np.arange(len(L)), n)
            start = np.cumsum(n) - n
            t = (np.arange(n.sum()) - start[line]) / (n[line] - 1)
            sx = P0[line, 0] + (P1[line, 0] - P0[line, 0]) * t
            sy = P0[line, 1] + (P1[line, 1] - P0[line, 1]) * t
            sz = L[line, 0, 2] + (L[line, 1, 2] - L[line, 0, 2]) * t
            lrole = np.array([role(int(c)) for c in lc])[line]
            offs = np.arange(lw) - (lw - 1) / 2
            for dx in offs:
                for dy in offs:
                    ix = np.floor(sx + dx).astype(int)
                    iy = np.floor(sy + dy).astype(int)
                    k = (ix >= 0) & (ix < big) & (iy >= 0) & (iy < big)
                    ix, iy, zz, rr = ix[k], iy[k], sz[k], lrole[k]
                    vis = zz + slack >= zbuf[iy, ix]
                    ix, iy, rr = ix[vis], iy[vis], rr[vis]
                    shade_b[iy, ix] = 1.0
                    role_b[iy, ix] = rr
                    covered[iy, ix] = True
        out = (shade_b, role_b, covered, codes)
        self._drawn_cache[file] = out
        return out

    def image(self, file: str, color: int):
        """A PIL image of the part in the colour, or None when the library hasn't the part."""
        from PIL import Image
        got = self._drawn(file)
        if got is None:
            return None
        shade, roles, inside, codes = got
        pal = [self._rgb(16, color), self._rgb(24, color)] + [self._rgb(c, color) for c in codes]
        pal = np.array(pal, dtype=np.float32)
        px = pal[roles]
        rgb = np.clip(px[..., :3] * shade[..., None], 0, 255)
        alpha = np.where(inside, px[..., 3], 0)
        a = np.dstack([rgb, alpha]).astype(np.uint8)
        # scaled down premultiplied, so the see-through background doesn't darken the outline
        return Image.fromarray(a, "RGBA").convert("RGBa").resize((SIZE, SIZE), Image.LANCZOS).convert("RGBA")

    def data_uri(self, file: str, color) -> str:
        if not isinstance(color, int):
            return ""
        try:
            img = self.image(file, color)
        except Exception:
            return ""
        if img is None:
            return ""
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

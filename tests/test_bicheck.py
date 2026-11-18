"""Small, self-contained tests: no LDraw library, no Studio tables, no network."""
import os
from collections import Counter
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bicheck import compare, ldraw, lxfml, report  # noqa: E402
from bicheck.mapping import Mapping  # noqa: E402

I = "1,0,0,0,1,0,0,0,1"


def brick(uuid, design, material, x, y, z):
    return (f'<Brick uuid="{uuid}" designID="{design};A"><Part uuid="p{uuid}" designID="{design};A" '
            f'partType="rigid" materials="{material}:0"><Bone transformation="{I},{x},{y},{z}"/></Part></Brick>')


def lxfml_text(bricks, steps_xml):
    return (f'<LXFML><Bricks>{"".join(bricks)}</Bricks><Bags><NumberedBag name="Bag 1">'
            + "".join(f'<Brick brickRef="{b.split(chr(34))[1]}"/>' for b in bricks)
            + f'</NumberedBag></Bags><BuildingInstructions><BuildingInstruction name="BI" uuid="bi1"><Steps>'
            f'{steps_xml}</Steps></BuildingInstruction></BuildingInstructions></LXFML>')


# 12 plates in a row (0.8 LDD = one stud = 20 LDU), a sub-build of 2 first
BRICKS = [brick(f"b{i}", "3023", "194" if i % 3 else "199", 0.8 * i, 0, 0) for i in range(12)]
STEPS = ('<Step><SubBuild name="s"><Step><In brickRef="b0"/></Step><Step><In brickRef="b1"/></Step></SubBuild></Step>'
         + "".join(f'<Step><In brickRef="b{i}"/><In brickRef="b{i + 1}"/></Step>' for i in range(2, 12, 2)))


def mapping():
    m = Mapping()
    m.colors = {"194": 71, "199": 72}
    m.color_names = {71: "Light_Bluish_Grey", 72: "Dark_Bluish_Grey", 4: "Red"}
    m.finish()
    return m


def test_steps_subbuild_first():
    bi = lxfml.read(lxfml_text(BRICKS, STEPS))
    # sub-build steps 1, 2, then the step that adds the sub-build, then 5 steps of two
    assert [s.path for s in bi.steps[1:4]] == ["1.1", "1.2", "1"]
    by = {p.brick: p.step for p in bi.parts}
    assert by["b0"] == 1 and by["b1"] == 2 and by["b2"] == 4 and by["b11"] == 8
    assert all(p.bag == "Bag 1" for p in bi.parts)


def model_text(colours, shift=(0, 0, 0), turn=False):
    """The same 12 plates in LDraw: LDD x -> LDraw x, in a submodel placed by the main model."""
    lines = ["0 FILE main.ldr", "0 Main"]
    M = "0 0 1 0 1 0 -1 0 0" if turn else "1 0 0 0 1 0 0 0 1"
    lines.append(f"1 16 {shift[0]} {shift[1]} {shift[2]} {M} row.ldr")
    lines += ["0 STEP", "0 FILE row.ldr", "0 Row"]
    for i, c in enumerate(colours):
        lines.append(f"1 {c} {20 * i} 0 0 1 0 0 0 1 0 0 0 1 3023.dat")
        if i % 4 == 3:
            lines.append("0 STEP")
    return "\n".join(lines)


def read_model(tmp_path, text):
    p = tmp_path / "m.mpd"
    p.write_text(text, encoding="utf-8")
    return ldraw.read(str(p))


def test_model_reader(tmp_path):
    md = read_model(tmp_path, model_text([71] * 12, shift=(5, 0, 0)))
    assert len(md.parts) == 12
    assert md.parts[0].submodel == "row.ldr" and md.parts[0].step == 1 and md.parts[4].step == 2
    assert np.allclose(md.parts[1].p, [25, 0, 0])
    assert md.parts[0].global_step == 1


def colours_ok():
    return [72 if i % 3 == 0 else 71 for i in range(12)]


def test_finds_wrong_colour_in_any_frame(tmp_path):
    bi = lxfml.read(lxfml_text(BRICKS, STEPS))
    cols = colours_ok()
    cols[7] = 72  # should be 71: step of b7 is 6 (steps 4..8 add b2..b11 two at a time)
    md = read_model(tmp_path, model_text(cols, shift=(300, -40, 120), turn=True))
    r = compare.compare(bi, md, mapping())
    wrong = [x for x in r.issues if x.kind == "colour"]
    assert len(wrong) == 1
    assert wrong[0].step == 6
    placed = r.model.parts[r.model_items[wrong[0].model].index]
    assert (placed.submodel, placed.step, placed.line) == ("row.ldr", 2, 15)  # the 8th plate
    assert placed.global_step == 2
    assert not [x for x in r.issues if x.kind in ("missing", "extra")]
    d = report.data(r)
    assert "WRONG COLOUR: wants 3023 Light Bluish Grey (71), model has 3023 Dark Bluish Grey (72)" in report.text(d)
    assert "step    6" in report.find(d, "3023", "dark bluish")


def test_missing_and_extra(tmp_path):
    bi = lxfml.read(lxfml_text(BRICKS, STEPS))
    md = read_model(tmp_path, model_text(colours_ok()[:11] + [4]))  # last one red: no such part there? same family
    r = compare.compare(bi, md, mapping())
    kinds = sorted(x.kind for x in r.issues)
    assert kinds == ["colour"]
    md = read_model(tmp_path, model_text(colours_ok()[:10]))  # two left out
    r = compare.compare(bi, md, mapping())
    assert sorted(x.kind for x in r.issues) == ["missing", "missing"]
    assert sorted(x.step for x in r.issues) == [8, 8]


# -- the booklets (PDF) way: steps and counts ----------------------------------------------
EL = {71: "4211398", 72: "4211063"}   # 3023 in Light / Dark Bluish Grey (Rebrickable's element table)


def booklets(colours):
    """A booklet that adds the 12 plates four at a time, with its parts list."""
    from bicheck import booklet
    steps = []
    for n in range(3):
        c = Counter(EL[x] for x in colours[4 * n:4 * n + 4])
        steps.append(booklet.Step(0, 10 + n, n + 1, 1, [(e, q, 0.0) for e, q in c.items()]))
    return booklet.Booklets(["b.pdf"], steps, dict(Counter(EL[x] for x in colours)))


def booklet_check(tmp_path, model_colours):
    from bicheck import stepmatch
    m = mapping()
    md = read_model(tmp_path, model_text(model_colours))
    bi, unknown = stepmatch.make_bi(booklets(colours_ok()), md, m, "test")
    assert not unknown
    return stepmatch.match(bi, md, m)


def test_booklets_match():
    from bicheck.elements import Elements
    E = Elements(mapping())
    assert E.get("4211398") == ("3023", 71) and E.get("4211063") == ("3023", 72)


def test_booklets_wrong_colour(tmp_path):
    cols = colours_ok()
    cols[5] = 72   # should be 71; the second step of the booklet
    r = booklet_check(tmp_path, cols)
    assert [x.kind for x in r.issues] == ["colour"]
    x = r.issues[0]
    assert r.bi.steps[x.step].label == "2"
    assert r.model.parts[r.model_items[x.model].index].line == 14   # the 6th plate
    d = report.data(r)
    assert d["steps"][x.step]["label"] == "2"


def test_booklets_missing(tmp_path):
    r = booklet_check(tmp_path, colours_ok()[:10])
    assert sorted(x.kind for x in r.issues) == ["missing", "missing"]
    assert {r.bi.steps[x.step].label for x in r.issues} == {"3"}
    assert not booklet_check(tmp_path, colours_ok()).issues


def test_booklet_links():
    from bicheck import getbooklets as g
    pasted = ('"https://www.lego.com/cdn/product-assets/product.bi.core.pdf/6672240.pdf" '
              'https://www.lego.com/cdn/product-assets/product.bi.core.pdf/6672242.pdf, https://example.com/x.html')
    assert [u.rsplit("/", 1)[1] for u in g.links_in(pasted)] == ["6672240.pdf", "6672242.pdf"]
    cdn = "https://www.lego.com/cdn/product-assets/product.bi.core.pdf/"
    items = [{"URL": cdn + "1.pdf", "description": "BI 3004/72 - 21373 V29 1/2"},
             {"URL": cdn + "2.pdf", "description": "BI 3004/72 - 21373 V29 2/2"},
             {"URL": cdn + "3.pdf", "description": "BI 3004/72 - 21373 V39 1/2"},
             {"URL": "https://evil.example/4.pdf", "description": "V39 2/2"}]
    assert g.pick(items) == [cdn + "1.pdf", cdn + "2.pdf"]   # one printing; only lego.com links


def test_detect_sets(tmp_path):
    from bicheck import detect
    assert detect.sets()["21373"] == "Downton Abbey"
    named = set(detect._named().values())
    assert "Downton Abbey" in named and "Fire Station" not in named   # a name many sets share
    cases = {"21373 - Downton Abbey.ldr": "21373", "850_fork_lift_truck.ldr": "850", "852-1.mpd": "852"}
    for name, sku in cases.items():
        p = tmp_path / name
        p.write_text("0 model\n1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n", encoding="utf-8")
        assert detect.guess(str(p))[0].sku == sku
    p = tmp_path / "castle.ldr"
    p.write_text("0 My castle\n0 Name: castle.ldr\n1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n", encoding="utf-8")
    assert detect.guess(str(p)) == []

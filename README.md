# bicheck

Finds where an LDraw model differs from the set's LEGO building instructions (the PDF
booklets from lego.com, or an LXFML instructions file), and in which step each
wrong part goes.

LDCad can tell you that you used three 3023 Dark Tan too few. bicheck tells you **where**:

```
step   58 [GF ext base right]  WRONG COLOUR: wants 3710 Dark Tan (28), model has 3710 Tan (19)
                               at main.ldr > east-wing.ldr, step 49, line 358
step  317 [GF ext base left]   WRONG PART: wants 3023 Dark Tan (28), model has 3069b Dark Tan (28)
                               at main.ldr, step 105, line 703
```

Set numbers and names come from [Rebrickable](https://rebrickable.com/downloads/)'s set list
(`bicheck/data/sets.csv.gz`, made with `packaging/make_sets.py`).

## Use

```
python -m bicheck                                     # a small window: pick the model, check the set and booklets
python -m bicheck model.mpd                           # the set read from the model's file/folder name, keywords or title,
                                                      # its booklets from Documents/bicheck/booklets/<set> or beside the model
python -m bicheck model.mpd book1.pdf book2.pdf       # the instruction booklets (PDF, from lego.com)
python -m bicheck model.mpd https://.../6672240.pdf   # or their links: downloaded to Documents/bicheck/booklets/<set>
python -m bicheck model.mpd --brickset-key KEY        # save a Brickset API key: missing booklets download by themselves
python -m bicheck model.io 21373                      # a BrickLink Studio model works too
python -m bicheck model.mpd instructions.lxfml        # or an LXFML instructions file (3D: positions too)
python -m bicheck model.mpd 21373 --find 3023 "dark tan"   # every step that uses a part in a colour
```

It prints a summary and writes `model.bicheck.html` beside the model (`--open` opens it):

Minifigure parts are kept apart (one summary line, a "Minifigures" filter in the report),
since figures are often left out or built differently. Sections placed apart or at an angle
(a car parked at 20 degrees) are lined up on their own, and LDraw assemblies such as
`3829c01` (steering wheel with holder) are compared part by part.

The report shows each part as a picture in its colour, drawn from your LDraw library
(`bicheck/thumbs.py`: a small offline renderer), with the instructions' part beside yours.

- **Mistakes by step**: each wrong part, grouped by instructions step, with the bag, what
  the instructions want, what the model has, and where it is in the model file (submodel,
  step, line). "Everything this step adds" lists the step's parts, so it is easy to find in
  the booklet.
- **Parts & colours**: the counts LDCad gives (instructions vs model) with every step that
  uses each part and colour, and every place in the model. Search it ("3023 dark tan").

## How it works: booklets (the default)

0. **Getting them** (`getbooklets.py`): lego.com doesn't let programs read a set's booklet list
   (403), but the PDFs download fine. The list comes from links the user pastes, or from
   Brickset's `getInstructions2` with the user's own free API key (one printing kept when a set
   is listed twice, "V29"/"V39"; only https lego.com PDF links are downloaded).

1. **Booklets** (`booklet.py`): every PDF of the set. The parts list on the last pages gives
   each part's picture, quantity and LEGO element number; each step's number (the booklet's
   most common big number style) has the step's parts box above it, a picture and "2x" per
   part. Each box picture is named by comparing it with the parts-list pictures, an element
   never used more often than the list says. Bag numbers are read too. Read once, then cached.
2. **Element numbers** (`elements.py`): Rebrickable's element table (shipped) gives the part
   and colour; colours are matched to LDraw by name, then by the nearest RGB.
3. **Matching** (`stepmatch.py`): the parts list gives the totals. Each model step (a
   submodel's `0 STEP`) is linked to the booklet steps that hold the same parts (rare parts
   count more, neighbouring booklet steps go together); equal parts are paired best-linked
   first, and the links are taken again from those pairs (so a sub-build built several times
   gets its own booklet steps per copy). Left-overs in linked steps pair up as a wrong colour
   (same part) or a wrong part (same colour); the rest is missing or extra. The model's steps
   don't need to follow the booklet. No positions: a right part in a wrong spot of its step
   is not seen.

## How it works: an LXFML instructions file

1. **Instructions**: an LXFML file on disk. Every brick has its final place, and the steps say when it is added. Steps
   are numbered in reading order, a sub-build's steps before the step that adds it.
   Spares and alternates (hidden at the end of the book) and stickers are left out.
2. **Model**: any .mpd/.ldr, with submodels (MPD sections or files beside it), or a
   BrickLink Studio .io (its model.ldr, which uses LDraw part names and colour codes).
   Parts are placed in world coordinates.
3. **Part numbers**: LEGO design ids are matched to LDraw files through BrickLink Studio's and
   LDD's tables (read from where they are installed) and Rebrickable's LDraw ids (snapshot
   shipped from tex2ldraw). tex2ldraw's own prints (`973p1083797.dat`) are recognised by name.
4. **Lining up**: the model may sit anywhere and face any way. The right-angled turn and shift
   that put the most identical parts on top of the instructions' win; sections moved as a
   whole (a floor lifted off, a submodel placed apart) are lined up one by one.
5. **Pairing**: every model part is paired with the instructions part in the same spot.
   A pair that differs is a mistake, with the instructions' step. What is left is missing or
   extra. If the model can't be lined up (built very differently), the counts and the step
   lists still work.
6. **Steps**: the model's own steps are linked to the instructions' steps they hold parts of.
   A wrong part or a left-over one is then paired again, within those linked steps only, with
   the same part in the same colour up to 2 studs away: two parts that traded places are
   reported once each as "swapped" (with where each belongs) instead of as wrong parts.

Prints are compared too, but shown apart ("print differs"): a plain part where the
instructions have a print LDraw lacks is common and not a building mistake.

## Needs

Python 3.10+, numpy, Pillow and PyMuPDF (to read the booklets). For part names and colour names: an LDraw library (found in
`~/Documents/LDraw`, `LDRAWDIR`, tex2ldraw's setting, or `--ldraw`). For the best part
matching: BrickLink Studio installed (or `--mapping` with its `ldraw_lxfml_mapping.json`).

## Tests

```
python -m pytest tests
python tests/make_mistakes.py correct.mpd 21373 --seed 1
```

`make_mistakes.py` takes a correct model (for example one tex2ldraw made from the same
instructions), puts in known mistakes (colours swapped for their neighbours, plates for
tiles, parts left out and added), turns and moves the whole model and lifts one submodel
off, then checks what bicheck finds. On Downton Abbey (21373, 4,736 parts) it finds all of
them, over 8 random seeds, in about 5 seconds a run.

## Releases

`.github/workflows/release.yml` builds `bicheck.exe` on Windows and zips it with
`packaging/build_exe.py` and `packaging/make_release.py`. To publish a release, set the version in
`bicheck/__init__.py` and `pyproject.toml`, then push a matching tag:

```
git tag v0.7.0
git push origin v0.7.0
```

Run the workflow by hand (Actions > release > Run workflow) to get the zips as a build artifact
without a release. Locally: `python packaging/build_exe.py`, then `python packaging/make_release.py`.

## Licence

MIT (see `LICENSE`). The tables in `bicheck/data` come from [Rebrickable](https://rebrickable.com/downloads/).

## Trademarks

LEGO® is a trademark of the LEGO Group, which does not sponsor, authorize or endorse bicheck. bicheck is an independent project, not affiliated with the LEGO Group, BrickLink, Rebrickable or Brickset; other product names are trademarks of their owners.

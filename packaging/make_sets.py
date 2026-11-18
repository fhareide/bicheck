"""Make bicheck/data/sets.csv.gz (set number, name, year, parts) from Rebrickable's sets.csv.gz.

    python packaging/make_sets.py path/to/sets.csv.gz

Rebrickable's download: https://rebrickable.com/downloads/ (sets.csv.gz). Only sets with a plain
number ("21373-1") and parts are kept.
"""
import csv
import gzip
import os
import re
import sys

src = sys.argv[1]
out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bicheck", "data", "sets.csv.gz")
with gzip.open(src, "rt", encoding="utf-8") as f:
    rows = [r for r in csv.DictReader(f)
            if re.fullmatch(r"\d{3,7}-1", r["set_num"]) and int(r["num_parts"] or 0) > 0]
rows.sort(key=lambda r: int(r["set_num"][:-2]))
with gzip.open(out, "wt", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["set", "name", "year", "parts"])
    for r in rows:
        w.writerow([r["set_num"][:-2], r["name"], r["year"], r["num_parts"]])
print(f"{out}: {len(rows)} sets")

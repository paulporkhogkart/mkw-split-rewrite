"""Pure 2D grid model for the clip sweep. No hardware imports."""
import re
from collections import namedtuple
import yaml

Cell = namedtuple("Cell", "slug display coord")   # coord = (row, col)


def to_filename(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^\w\s'-]", "", slug)
    slug = re.sub(r"[']+", "", slug)
    slug = re.sub(r"[\s-]+", "_", slug.strip())
    return slug


def _char_slug(label: str) -> str:
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", label)
    if m:
        return f"{to_filename(m.group(1))}__{to_filename(m.group(2))}"
    return f"{to_filename(label)}__base"


class Grid:
    def __init__(self, rows_by_cat: dict):
        self._cells = {}
        self._by_slug = {}
        for cat, rows in rows_by_cat.items():
            slugify = _char_slug if cat == "characters" else to_filename
            cells = []
            for r, row in enumerate(rows):
                for c, label in enumerate(row):
                    cell = Cell(slugify(label), label, (r, c))
                    cells.append(cell)
                    if (cat, cell.slug) in self._by_slug:
                        raise ValueError(f"duplicate cell slug {cell.slug!r} in {cat!r}")
                    self._by_slug[(cat, cell.slug)] = cell
            self._cells[cat] = cells

    def cells(self, category: str) -> list:
        return self._cells[category]

    def _cat_of(self, slug: str) -> str:
        for cat in self._cells:
            if (cat, slug) in self._by_slug:
                return cat
        raise KeyError(slug)

    def coord_of(self, slug: str) -> tuple:
        return self._by_slug[(self._cat_of(slug), slug)].coord

    def span_of(self, slug: str) -> tuple:
        """(#rows, widest row) for the category containing `slug`. Used to bound nav steps:
        a closed-loop traversal is at most (rows-1)+(width-1) presses, so the step budget
        must exceed that. Auto-scales if the roster grows (no hard-coded cap to outgrow)."""
        cells = self._cells[self._cat_of(slug)]
        rows = max(c.coord[0] for c in cells) + 1
        width = max(c.coord[1] for c in cells) + 1
        return (rows, width)

    def sweep_steps(self, category: str) -> list:
        steps, prev = [], None
        for cell in self._cells[category]:
            if prev is None:
                presses = []
            elif cell.coord[0] == prev.coord[0]:
                presses = ["DPAD_RIGHT"]
            else:                                  # new row: right onto blank, then down
                presses = ["DPAD_RIGHT", "DPAD_DOWN"]
            steps.append((cell.slug, presses))
            prev = cell
        return steps

    def horizontal_delta(self, from_slug: str, to_slug: str) -> list:
        cat = self._cat_of(from_slug)
        (r0, c0) = self._by_slug[(cat, from_slug)].coord
        (r1, c1) = self._by_slug[(cat, to_slug)].coord
        if r0 != r1:
            raise ValueError(f"{from_slug} and {to_slug} are not in the same row")
        d = c1 - c0
        return ["DPAD_RIGHT"] * d if d > 0 else ["DPAD_LEFT"] * (-d)


def load_grid(path: str) -> Grid:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Grid(data)

"""Parse full_capture.yaml's flow into clip_sweep.yaml grid rows (single language).

Char cells = the `char`(+optional `costume`) steps between tell:character_screen and
the confirm_char marker; kart cells = the `kart` steps between tell:kart_screen and
confirm_kart. A `DPAD_DOWN` press inside a section starts a new row.
"""
import os
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "scripts", "full_capture.yaml")
OUT = os.path.join(HERE, "scripts", "clip_sweep.yaml")


def _cell_label(step):
    if "char" in step:
        c = str(step["char"])
        return f"{c} ({step['costume']})" if "costume" in step else c
    if "kart" in step:
        return str(step["kart"])
    return None


def _section_rows(flow, start_tell, end_marker):
    rows, row, active = [], [], False
    for step in flow:
        if not isinstance(step, dict):
            continue
        if step.get("tell") == start_tell:
            active = True
            continue
        if active and step.get("_marker") == end_marker:
            break
        if not active:
            continue
        if step.get("press") == "DPAD_DOWN" and row:
            rows.append(row)
            row = []
        label = _cell_label(step)
        if label:
            row.append(label)
    if row:
        rows.append(row)
    return rows


def build():
    with open(SRC, encoding="utf-8") as f:
        script = yaml.safe_load(f)
    flow = script["flow"]
    data = {
        "characters": _section_rows(flow, "character_screen", "confirm_char"),
        "karts": _section_rows(flow, "kart_screen", "confirm_kart"),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=200)
    nch = sum(len(r) for r in data["characters"])
    nk = sum(len(r) for r in data["karts"])
    print(f"wrote {OUT}: {nch} char cells in {len(data['characters'])} rows, "
          f"{nk} karts in {len(data['karts'])} rows")


if __name__ == "__main__":
    build()

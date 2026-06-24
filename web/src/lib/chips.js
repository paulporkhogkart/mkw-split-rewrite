// Map an activity store row to render-ready chips. The single slug authority for chip
// filenames: mirrors pi/src/db/slug.ts:slugify exactly (capture basenames already match it).
// Server events carry DISPLAY names; we slugify here. Missing assets are hidden at render.

export function slugify(name) {
  return String(name ?? "")
    .toLowerCase()
    .replace(/[\u2018\u2019']/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

export function chipUrl(category, slug) {
  return `/chips/${category}/${slug}.png`;
}

function comboSlug(character, costume) {
  const c = slugify(character);
  if (!c) return null;
  const k = costume ? slugify(costume) : "base";
  return `${c}__${k}`;
}

function courseChip(course) {
  return course?.slug
    ? [{ src: chipUrl("courses", course.slug), fallback: null, alt: course.name ?? "" }]
    : [];
}

function characterChip(character, costume) {
  const combo = comboSlug(character, costume);
  if (!combo) return [];
  const base = comboSlug(character, null);
  return [{ src: chipUrl("combos", combo), fallback: chipUrl("combos", base), alt: character ?? "" }];
}

/** Ordered chips (course, kart, character) for a store row; [] when none apply. */
export function chipsFor(row) {
  if (!row) return [];
  if (row.kind === "session") {
    if (row.cls !== "racing") return [];
    return [...courseChip(row.course), ...characterChip(row.character, row.costume)];
  }
  const e = row.event;
  if (!e) return [];
  switch (e.type) {
    case "pb":
    case "rank": {
      const pay = e.payload || {};
      const kart = pay.kart
        ? [{ src: chipUrl("karts", slugify(pay.kart)), fallback: null, alt: pay.kart }]
        : [];
      return [...courseChip(e.course), ...kart, ...characterChip(pay.character, pay.costume)];
    }
    case "turf_claim":
    case "turf_fire":
    case "turf_waver":
    case "wr":
      return courseChip(e.course);
    default:
      return [];
  }
}

/** Combine two activity-event lists into one newest-first list: dedup by id, sort by id DESC, cap. */
export function mergeActivity(existing, incoming, cap = 300) {
  const byId = new Map();
  for (const ev of existing) byId.set(ev.id, ev);
  for (const ev of incoming) byId.set(ev.id, ev);
  return [...byId.values()].sort((a, b) => b.id - a.id).slice(0, cap);
}

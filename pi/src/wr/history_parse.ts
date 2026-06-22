import { parse, type HTMLElement } from 'node-html-parser';
import { mkwrsTimeToMs, msToTimeStr } from './time';
import { lapTimeToMs, parsePerLap } from './lap';

/** Pre-release rows ("Time set before 2025-06-05 00:00 UTC") get a sentinel just before release. */
const RELEASE_SENTINEL = '2025-06-04T00:00:00.000Z';

export type ScrapedHistoryRow = {
  recordMs: number;
  recordStr: string;
  dateIso: string | null;
  datePrecision: 'day' | 'pre_release';
  holderName: string | null;
  holderKey: string | null;
  nation: string | null;
  lapSplitsMs: (number | null)[];
  coins: number[] | null;
  mushrooms: number[] | null;
  characterRaw: string | null;
  kartRaw: string | null;
  videoUrl: string | null;
};

type Layout = { laps: number; stacked: boolean };

/** The history table is the `table.wr` with the most rows (the current-WR table has 1 record). */
function selectHistoryTable(root: HTMLElement): HTMLElement | null {
  const tables = root.querySelectorAll('table.wr');
  if (tables.length === 0) return null;
  return tables.reduce((a, b) =>
    b.querySelectorAll('tr').length > a.querySelectorAll('tr').length ? b : a);
}

function detectLayout(headerTr: HTMLElement): Layout {
  const ths = headerTr.querySelectorAll('th').map((th) => th.text.trim());
  const laps = ths.filter((t) => /^Lap \d+$/.test(t)).length;
  const stacked = ths.some((t) => /Coins & Shrooms/i.test(t) || /Combination/i.test(t));
  return { laps, stacked };
}

function isPatchRow(tds: HTMLElement[]): boolean {
  return tds.some((td) => td.getAttribute('colspan') != null);
}

function parseDate(td: HTMLElement): { iso: string | null; precision: 'day' | 'pre_release' } {
  const span = td.querySelector('span');
  if (span && /pre-?release/i.test(span.text)) return { iso: RELEASE_SENTINEL, precision: 'pre_release' };
  const txt = td.text.trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(txt)) return { iso: `${txt}T00:00:00.000Z`, precision: 'day' };
  return { iso: null, precision: 'day' };
}

function parseTimeCell(td: HTMLElement): { ms: number; videoUrl: string | null } | null {
  const a = td.querySelector('a');
  const txt = (a?.text ?? td.text).trim();
  let ms: number;
  try { ms = mkwrsTimeToMs(txt); } catch { return null; }
  return { ms, videoUrl: a?.getAttribute('href') ?? null };
}

function parsePlayer(td: HTMLElement): { name: string | null; key: string | null } {
  const a = td.querySelector('a');
  const name = (a?.text ?? td.text).trim() || null;
  const m = /player=([^&"]+)/.exec(a?.getAttribute('href') ?? '');
  return { name, key: m ? m[1] : null };
}

function parseNation(td: HTMLElement): string | null {
  const img = td.querySelector('img');
  if (!img) return null;
  const m = /([A-Za-z]{2,3})\.png$/.exec(img.getAttribute('src') ?? '');
  return m ? m[1] : (img.getAttribute('alt') || null);
}

/** Split `Character (Costume)` on the LAST parenthesis group; a bare name is base costume. */
export function splitCharacter(raw: string): { character: string | null; costume: string | null } {
  const t = (raw ?? '').trim();
  if (!t || t === '-') return { character: null, costume: null };
  const m = /^(.*?)\s*\(([^)]*)\)\s*$/.exec(t);
  return m ? { character: m[1].trim(), costume: m[2].trim() } : { character: t, costume: null };
}

function buildRow(layout: Layout, primary: HTMLElement[], cont: HTMLElement[]): ScrapedHistoryRow | null {
  const n = layout.laps;
  const time = parseTimeCell(primary[1]);
  if (!time) return null;                                   // unparseable time → not a record
  const date = parseDate(primary[0]);
  const player = parsePlayer(primary[2]);
  const lapSplitsMs: (number | null)[] = [];
  for (let k = 0; k < n; k++) lapSplitsMs.push(lapTimeToMs(primary[5 + k]?.text.trim() ?? ''));

  let coins: number[] | null, mushrooms: number[] | null;
  let characterRaw: string | null, kartRaw: string | null;
  if (layout.stacked) {
    coins = parsePerLap(primary[5 + n]?.text.trim() ?? '');
    characterRaw = primary[6 + n]?.text.trim() || null;
    mushrooms = cont[0] ? parsePerLap(cont[0].text.trim()) : null;
    kartRaw = cont[1]?.text.trim() || null;
  } else {
    coins = parsePerLap(primary[5 + n]?.text.trim() ?? '');
    mushrooms = parsePerLap(primary[6 + n]?.text.trim() ?? '');
    characterRaw = primary[7 + n]?.text.trim() || null;
    kartRaw = primary[8 + n]?.text.trim() || null;
  }
  return {
    recordMs: time.ms, recordStr: msToTimeStr(time.ms),
    dateIso: date.iso, datePrecision: date.precision,
    holderName: player.name, holderKey: player.key, nation: parseNation(primary[3]),
    lapSplitsMs, coins, mushrooms, characterRaw, kartRaw, videoUrl: time.videoUrl,
  };
}

/** Parse a display.php page's full WR history. Returns rows in page order (oldest → newest). */
export function parseHistory(html: string): ScrapedHistoryRow[] {
  const root = parse(html);
  const table = selectHistoryTable(root);
  if (!table) return [];
  const rows = table.querySelectorAll('tr');
  if (rows.length < 2) return [];
  const layout = detectLayout(rows[0]);
  const data = rows.slice(1);
  const out: ScrapedHistoryRow[] = [];

  for (let i = 0; i < data.length; i++) {
    const tds = data[i].querySelectorAll('td');
    if (tds.length === 0) continue;                         // stray header row
    if (isPatchRow(tds)) continue;                          // patch/info row

    if (layout.stacked) {
      if (tds[0].getAttribute('rowspan') == null) continue; // orphan continuation → skip
      const next = data[i + 1]?.querySelectorAll('td') ?? [];
      const cont = (next.length === 2 && !isPatchRow(next)) ? next : [];
      const row = buildRow(layout, tds, cont);
      if (row) out.push(row);
      i++;                                                  // consume the continuation row
    } else {
      const row = buildRow(layout, tds, []);
      if (row) out.push(row);
    }
  }
  return out;
}

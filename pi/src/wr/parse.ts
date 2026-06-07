import { parse } from 'node-html-parser';
import { mkwrsTimeToMs, msToTimeStr } from './time';

export type ScrapedWr = {
  courseName: string;
  recordMs: number;
  recordStr: string;
  holder: string | null;
  date: string | null;
  character: string | null;
  vehicle: string | null;
  videoUrl: string | null;
};

const cellText = (el: { querySelector: (s: string) => any; text: string } | null): string =>
  (el ? (el.querySelector('a')?.text ?? el.text) : '').trim();

export function parseWrTable(html: string): ScrapedWr[] {
  const root = parse(html);
  const table = root.querySelector('table.wr');
  if (!table) throw new Error('mkwrs: table.wr not found');
  const out: ScrapedWr[] = [];
  for (const tr of table.querySelectorAll('tr')) {
    const td = tr.querySelectorAll('td');
    if (td.length < 9) continue;                       // header / short rows
    const courseName = cellText(td[0]);
    if (!courseName || /\(glitch\)/i.test(courseName)) continue;
    const timeLink = td[1].querySelector('a');
    const timeText = (timeLink?.text ?? td[1].text).trim();
    let recordMs: number;
    try { recordMs = mkwrsTimeToMs(timeText); } catch { continue; }   // unparseable -> skip row
    out.push({
      courseName,
      recordMs,
      recordStr: msToTimeStr(recordMs),
      holder: cellText(td[2]) || null,
      date: td[4].text.trim() || null,
      character: td[6].text.trim() || null,
      vehicle: td[7].text.trim() || null,
      videoUrl: timeLink?.getAttribute('href') ?? null,
    });
  }
  return out;
}

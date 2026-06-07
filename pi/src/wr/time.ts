/** Parse a mkwrs time like `1'47"414` into milliseconds. ms field is 1-3 digits
 *  (thousandths/hundredths/tenths) and is normalized to 3 digits. Throws if malformed. */
export function mkwrsTimeToMs(raw: string): number {
  const m = /^(\d+)'(\d{1,2})"(\d{1,3})$/.exec(raw.trim());
  if (!m) throw new Error(`unparseable mkwrs time: ${JSON.stringify(raw)}`);
  const min = Number(m[1]);
  const sec = Number(m[2]);
  const ms = Number(m[3].padEnd(3, '0'));
  return min * 60000 + sec * 1000 + ms;
}

/** Format milliseconds as canonical `M:SS.mmm`. */
export function msToTimeStr(ms: number): string {
  const min = Math.floor(ms / 60000);
  const sec = Math.floor((ms % 60000) / 1000);
  const rem = ms % 1000;
  return `${min}:${String(sec).padStart(2, '0')}.${String(rem).padStart(3, '0')}`;
}

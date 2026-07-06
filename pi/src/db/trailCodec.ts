// Lossless at-rest trail codec (v1). Spec: docs/superpowers/specs/2026-07-07-trail-storage-compression-design.md
// The floats are full-mantissa doubles; they are moved as BYTES only — no arithmetic ever
// touches them, so bit-exactness is structural. NaN/Inf cannot exist in stored trails
// (JSON can't carry them and SQLite turns NaN into NULL against a NOT NULL column), so
// packTrail treats non-finite input as malformed.
import * as zlib from 'node:zlib';

/** One trail point, exactly the row shape readers consume today. */
export type TrailPoint = { t_ms: number; cx: number; cy: number; score: number; lap: number | null };

/** `run_trails.codec` value: v1 packed payload, brotli-compressed. */
export const CODEC_BROTLI_V1 = 1;

const LAP_NULL = 255;

function pushVarint(out: number[], v: number): void {
  if (!Number.isSafeInteger(v) || v < 0) throw new Error(`varint out of range: ${v}`);
  while (v > 127) { out.push((v & 127) | 128); v = Math.floor(v / 128); }
  out.push(v);
}

function readVarint(buf: Buffer, pos: { i: number }): number {
  let v = 0, shift = 1, b: number;
  do {
    b = buf[pos.i++];
    if (b === undefined) throw new Error('varint past end of buffer');
    v += (b & 127) * shift; shift *= 128;
  } while (b & 128);
  if (!Number.isSafeInteger(v)) throw new Error('varint overflow');
  return v;
}

/** n float64s → byte-plane-transposed buffer: plane k holds byte k (LSB→MSB) of every value. */
function shuffleF64(vals: number[]): Buffer {
  const n = vals.length, flat = Buffer.alloc(n * 8), out = Buffer.alloc(n * 8);
  for (let i = 0; i < n; i++) flat.writeDoubleLE(vals[i], i * 8);
  for (let i = 0; i < n; i++) for (let k = 0; k < 8; k++) out[k * n + i] = flat[i * 8 + k];
  return out;
}

function unshuffleF64(buf: Buffer, n: number): number[] {
  const flat = Buffer.alloc(n * 8);
  for (let i = 0; i < n; i++) for (let k = 0; k < 8; k++) flat[i * 8 + k] = buf[k * n + i];
  const out = new Array<number>(n);
  for (let i = 0; i < n; i++) out[i] = flat.readDoubleLE(i * 8);
  return out;
}

/** Uncompressed v1 payload. Throws on malformed input (n=0, non-monotonic/negative/
 *  non-integer t_ms, lap outside null∪[0,254], non-finite floats) rather than pack it. */
export function packTrail(pts: TrailPoint[]): Buffer {
  if (pts.length === 0) throw new Error('empty trail');
  const head: number[] = [];
  pushVarint(head, pts.length);
  head.push(0);                                       // flags: reserved, always 0 in v1
  let prev = -1;
  for (const p of pts) {
    if (!Number.isSafeInteger(p.t_ms) || p.t_ms < 0) throw new Error(`bad t_ms: ${p.t_ms}`);
    if (p.t_ms <= prev) throw new Error(`t_ms not strictly increasing at ${p.t_ms}`);
    if (!Number.isFinite(p.cx) || !Number.isFinite(p.cy) || !Number.isFinite(p.score))
      throw new Error('non-finite coordinate/score');
    pushVarint(head, prev < 0 ? p.t_ms : p.t_ms - prev);
    prev = p.t_ms;
  }
  let i = 0;
  while (i < pts.length) {                            // lap RLE: (u8 value, varint count)
    const v = pts[i].lap;
    if (v !== null && (!Number.isInteger(v) || v < 0 || v > 254)) throw new Error(`bad lap: ${v}`);
    let j = i;
    while (j < pts.length && pts[j].lap === v) j++;
    head.push(v === null ? LAP_NULL : v);
    pushVarint(head, j - i);
    i = j;
  }
  return Buffer.concat([
    Buffer.from(head),
    shuffleF64(pts.map((p) => p.cx)),
    shuffleF64(pts.map((p) => p.cy)),
    shuffleF64(pts.map((p) => p.score)),
  ]);
}

export function unpackTrail(buf: Buffer): TrailPoint[] {
  const pos = { i: 0 };
  const n = readVarint(buf, pos);
  if (n === 0) throw new Error('empty trail blob');
  pos.i++;                                            // flags (reserved)
  const t = new Array<number>(n);
  for (let k = 0; k < n; k++) t[k] = k === 0 ? readVarint(buf, pos) : t[k - 1] + readVarint(buf, pos);
  const lap = new Array<number | null>(n);
  let filled = 0;
  while (filled < n) {
    const byte = buf[pos.i++];
    if (byte === undefined) throw new Error('lap RLE past end of buffer');
    const count = readVarint(buf, pos);
    if (filled + count > n) throw new Error('lap RLE overrun');
    const v = byte === LAP_NULL ? null : byte;
    for (let k = 0; k < count; k++) lap[filled++] = v;
  }
  if (buf.length !== pos.i + n * 24) throw new Error(`blob length ${buf.length} != expected ${pos.i + n * 24}`);
  const cx = unshuffleF64(buf.subarray(pos.i, pos.i + n * 8), n);
  const cy = unshuffleF64(buf.subarray(pos.i + n * 8, pos.i + n * 16), n);
  const score = unshuffleF64(buf.subarray(pos.i + n * 16, pos.i + n * 24), n);
  return t.map((t_ms, k) => ({ t_ms, cx: cx[k], cy: cy[k], score: score[k], lap: lap[k] }));
}

export function encodeTrail(pts: TrailPoint[]): Buffer {
  const packed = packTrail(pts);
  return zlib.brotliCompressSync(packed, { params: {
    [zlib.constants.BROTLI_PARAM_QUALITY]: 11,
    [zlib.constants.BROTLI_PARAM_SIZE_HINT]: packed.length,
  } });
}

export function decodeTrail(data: Uint8Array): TrailPoint[] {
  return unpackTrail(zlib.brotliDecompressSync(data));
}

/** Bit-exact equality gate (float64 BITS, not ==). Throws at the first mismatch. */
export function assertTrailsIdentical(a: TrailPoint[], b: TrailPoint[]): void {
  if (a.length !== b.length) throw new Error(`length ${a.length} != ${b.length}`);
  const ba = Buffer.alloc(8), bb = Buffer.alloc(8);
  for (let i = 0; i < a.length; i++) {
    if (a[i].t_ms !== b[i].t_ms || a[i].lap !== b[i].lap) throw new Error(`t/lap mismatch at ${i}`);
    for (const f of ['cx', 'cy', 'score'] as const) {
      ba.writeDoubleLE(a[i][f], 0); bb.writeDoubleLE(b[i][f], 0);
      if (!ba.equals(bb)) throw new Error(`${f} bits mismatch at ${i}`);
    }
  }
}

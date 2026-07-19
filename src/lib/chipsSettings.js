// Pure display helpers for the Chips settings tab (ChipsSettings.svelte owns IPC/DOM).

export function fmtBytes(n) {
  if (!n) return "0 B";
  // B/KB/MB scale binary (1024, matching what a file listing shows); GB/TB switch to
  // decimal (1e9, matching how the pack's advertised download size is quoted) — the two
  // regimes only ever meet at the GB boundary, well above any single cached asset.
  if (n < 1_000_000_000) {
    const units = ["B", "KB", "MB"];
    let i = 0, v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    const d = v >= 100 ? 0 : v >= 10 ? 1 : 2;
    return `${Number(v.toFixed(d))} ${units[i]}`;
  }
  const units = ["GB", "TB"];
  let i = 0, v = n / 1e9;
  while (v >= 1000 && i < units.length - 1) { v /= 1000; i++; }
  const d = v >= 100 ? 0 : v >= 10 ? 1 : 2;
  return `${Number(v.toFixed(d))} ${units[i]}`;
}

const OFFER = "Download full pack (6.3 GB)";

export function packLabel(status, progress) {
  const shard = progress && progress.total ? ` · shard ${Math.min(progress.done + 1, progress.total)}/${progress.total}` : "";
  if (status.packPaused && status.packWanted) return `Paused${shard}`;
  if (status.packWanted && !status.packComplete) return `Downloading${shard}`;
  if (status.packComplete && status.updateAvailable) return `Pack update available (6.3 GB)`;
  if (status.packComplete) return `Installed (${status.packTag})`;
  return OFFER;
}

export function progressFrac(progress) {
  if (!progress || !progress.total) return null;
  return Math.min(1, Math.max(0, progress.done / progress.total));
}

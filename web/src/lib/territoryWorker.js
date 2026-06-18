// Off-main-thread territory render. Reads the coverage + base bitmaps, builds the
// RGBA layer at full asset resolution, and ships an ImageBitmap back to the page.
import { buildTerritory } from "./territory.js";

function readRGBA(bitmap, W, H) {
  const c = new OffscreenCanvas(W, H), x = c.getContext("2d");
  x.drawImage(bitmap, 0, 0, W, H);
  return x.getImageData(0, 0, W, H).data;
}

self.onmessage = async (e) => {
  const { coverageBitmap, baseBitmap, W, H, manifestCourses, territoryRows } = e.data;
  const cd = readRGBA(coverageBitmap, W, H);
  const coverage = new Uint8Array(W * H);
  for (let p = 0; p < W * H; p++) coverage[p] = cd[p * 4];     // R channel = grayscale coverage
  const terr = readRGBA(baseBitmap, W, H);
  const rgba = buildTerritory({ coverage, W, H, terr, manifestCourses, territoryRows });
  const bitmap = await createImageBitmap(new ImageData(rgba, W, H));
  self.postMessage({ bitmap }, [bitmap]);
};

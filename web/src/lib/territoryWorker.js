// Off-main-thread territory render. Reads the coverage + base bitmaps, builds the
// RGBA layer at full asset resolution, and ships an ImageBitmap back to the page.
import { buildTerritory } from "./territory.js";

function readRGBA(bitmap, W, H) {
  const c = new OffscreenCanvas(W, H), x = c.getContext("2d");
  x.drawImage(bitmap, 0, 0, W, H);
  return x.getImageData(0, 0, W, H).data;
}

self.onmessage = async (e) => {
  const { coverageBitmap, baseBitmap, W, H, targetW, targetH, manifestCourses, territoryRows } = e.data;
  const rw = targetW || W, rh = targetH || H;                  // render at the caller's target size
  const cd = readRGBA(coverageBitmap, rw, rh);
  const coverage = new Uint8Array(rw * rh);
  for (let p = 0; p < rw * rh; p++) coverage[p] = cd[p * 4];   // R channel = grayscale coverage
  const terr = readRGBA(baseBitmap, rw, rh);
  const rgba = buildTerritory({ coverage, W: rw, H: rh, terr, manifestCourses, territoryRows });
  const bitmap = await createImageBitmap(new ImageData(rgba, rw, rh));
  self.postMessage({ bitmap }, [bitmap]);
};

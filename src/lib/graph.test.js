import { describe, it, expect } from "vitest";
import { GRAPH_NODES, GRAPH_NODE_MAP } from "./graph.js";

describe("screen graph nodes", () => {
  it("includes a NO_SIGNAL node so it is editable in the screen graph", () => {
    // The graph renders from GRAPH_NODES, not from SCREEN_NAMES — a screen must
    // have an entry here to appear (and be clickable to edit) in Edit Screens.
    const n = GRAPH_NODE_MAP["NO_SIGNAL"];
    expect(n).toBeTruthy();
    expect(n.label).toBeTruthy();
  });

  it("has unique node ids", () => {
    const ids = GRAPH_NODES.map((n) => n.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("includes PHOTO_MODE and EXIT_PHOTO_MODE so photo mode is editable", () => {
    expect(GRAPH_NODE_MAP["PHOTO_MODE"]?.label).toBeTruthy();
    expect(GRAPH_NODE_MAP["EXIT_PHOTO_MODE"]?.label).toBeTruthy();
  });

  it("includes GAMECHAT (universal overlay) so it is editable", () => {
    expect(GRAPH_NODE_MAP["GAMECHAT"]?.label).toBeTruthy();
  });

  it("includes GALLERY_VIEW (Album photo viewer, universal overlay) so it is editable", () => {
    expect(GRAPH_NODE_MAP["GALLERY_VIEW"]?.label).toBeTruthy();
  });
});

describe("screen graph edges", () => {
  it("wires photo mode to/from the race flow", async () => {
    const { GRAPH_EDGES } = await import("./graph.js");
    const has = (a, b) => GRAPH_EDGES.some((e) => e[0] === a && e[1] === b);
    expect(has("RACING", "PHOTO_MODE")).toBe(true);
    expect(has("PHOTO_MODE", "EXIT_PHOTO_MODE")).toBe(true);
    expect(has("EXIT_PHOTO_MODE", "RACING")).toBe(true);
  });
});

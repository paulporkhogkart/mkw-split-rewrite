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
});

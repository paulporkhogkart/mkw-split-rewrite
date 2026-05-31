// JS color palette — mirrors the CSS tokens in src/theme.css for contexts that
// cannot read CSS vars: the canvas 2D context, inline SVG fills, JS-returned styles.
// Keep in sync with theme.css :root.
export const C = {
  bg:'#1b1c1e', panel:'#202023', panel2:'#26272b', raised:'#2e2f33', well:'#161718',
  bd:'#34353a', bdSoft:'#27282b', feed:'#0b0c0e', track:'#303135',
  tx:'#d9dadd', txMut:'#9a9ca1', txDim:'#6b6d73',
  accent:'#3d7cc2', accentSoft:'#2d5e94', accentBg:'#26303c',
  ok:'#5aa86a', warn:'#c89a3e', err:'#cf5b4e', idle:'#56585e',
  roiCtx:'#82858b',   // neutral sibling/context ROI box on the feed overlay

  // Minimap tracking states — mirror mkw_tracker/overlay/minimap.py semantics.
  mmRingFace:'#e6c84a',   // tracking: ring + face confirmed (yellow)
  mmRingOnly:'#d98a3c',   // ring only: hazard / icon-swap (orange)
  mmReacquire:'#e0b84a',  // reacquiring after jump / respawn (amber)
};

// Muted, distinct hues to tell separate replay trails apart on the minimap.
export const REPLAY_HUES = ['#6f9ed6', '#c98f5a', '#7faf8a', '#b08fc9', '#c98f9e'];

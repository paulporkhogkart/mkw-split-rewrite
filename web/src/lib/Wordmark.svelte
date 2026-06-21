<script>
  // THE KART-OFF brand wordmark. Geometry comes from wordmark.config.json (authored in
  // temp/wordmark-editor.html), applied as CSS vars so a re-export needs no code change.
  //
  //  - no `player`            -> plain mark: red OFF tag, no figure (used everywhere but the navbar)
  //  - `player` set           -> that player's figure sits over the A + the OFF tag takes their colour
  //  - `fire` (with a player) -> swaps to the on-fire figure + placement (navbar hover)
  //
  // Everything is em-relative, so the mark scales from any `size` and matches the editor placements
  // without runtime measurement.
  import config from "./wordmark.config.json";
  import { figureFor, onpaceFigure } from "../../../src/lib/playerFigures.js";

  export let size = "22px";
  export let player = null;     // roster name (lowercase) | null
  export let fire = false;      // on-fire variant (only meaningful with a player)

  const RED = "#ff4438";
  const t = config.the;

  $: pcfg = player ? config.players[player] : null;
  $: slot = pcfg ? (fire ? pcfg.fire : pcfg.default) : null;
  $: figUrl = player ? (fire ? onpaceFigure(player) : figureFor(player, true)) : null;
  $: offBg = pcfg ? pcfg.color : RED;

  // THE geometry -> CSS vars on the root.
  $: theVars =
    `--the-fs:${t.fontSize};--the-ls:${t.letterSpacing};--the-rot:${t.rotate};` +
    `--the-x:${t.nudgeX};--the-y:${t.nudgeY};--the-sx:${t.scaleX};` +
    `--the-w:${t.slotW};--the-h:${t.slotH};--the-b:${t.lift};--the-mr:${t.gap};` +
    `--the-color:${t.color};--off-bg:${offBg};`;

  // Figure placement over the A (em units; mirrors the editor's centre+offset+rotate).
  $: figStyle = slot
    ? `height:${slot.scale}em;transform:translate(calc(-50% + ${slot.x}em),calc(-50% + ${slot.y}em)) rotate(${slot.rotate}deg);`
    : "";
</script>

<span class="wm" style="font-size:{size};{theVars}" aria-label="THE KART-OFF">
  <span class="the" aria-hidden="true"><span class="tw">THE</span></span><span class="kart"
    ><span class="k">K</span><span class="aw"><span class="a">A</span
      >{#if figUrl}<img class="afig" src={figUrl} style={figStyle} alt="" aria-hidden="true" draggable="false" />{/if}</span
    ><span class="rt">RT</span></span
  ><span class="off">OFF</span>
</span>

<style>
  .wm{
    display:inline-flex; align-items:baseline; white-space:nowrap;
    font-family:'Inter', system-ui, sans-serif; font-weight:900;
    color:#f3f4f6; letter-spacing:-.022em; line-height:1; user-select:none;
  }
  /* THE: the whole word rotated (reads bottom-to-top) so the letters keep a shared baseline +
     natural spacing. Sized via vars from the config; the fixed slot reserves the horizontal room. */
  .the{
    position:relative; display:inline-block; align-self:flex-end;
    width:var(--the-w); height:var(--the-h); bottom:var(--the-b); margin-right:var(--the-mr);
  }
  .the .tw{
    position:absolute; left:50%; top:50%;
    transform:translate(calc(-50% + var(--the-x)), calc(-50% + var(--the-y))) rotate(var(--the-rot)) scaleX(var(--the-sx));
    transform-origin:center; white-space:nowrap; font-weight:800; font-size:var(--the-fs);
    letter-spacing:var(--the-ls); line-height:1; color:var(--the-color);
  }
  .kart{ display:inline-flex; }
  /* The A anchors the player figure; the figure floats over it, centred + offset per config. */
  .aw{ position:relative; display:inline-block; }
  .afig{
    position:absolute; left:50%; top:50%; width:auto; max-width:none;
    transform-origin:center; pointer-events:none; z-index:2;
  }
  /* OFF: knocked out of the tag — red by default, the player's colour in the navbar. */
  .off{
    align-self:center; background:var(--off-bg); color:#141517; font-weight:900;
    padding:.05em .13em .09em; margin-left:.15em; line-height:.74;
    border-radius:1.5px; letter-spacing:-.02em;
  }
</style>

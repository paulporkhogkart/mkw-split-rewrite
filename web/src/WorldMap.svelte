<script>
  import { onMount } from "svelte";
  import { baseUrl, manifestUrl, spriteUrl, hitStyle, spriteStyle } from "./lib/map.js";

  let manifest = null;
  let error = false;

  onMount(async () => {
    try {
      const r = await fetch(manifestUrl(), { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      manifest = await r.json();
    } catch (e) {
      console.error("world map: manifest load failed", e);
      error = true;
    }
  });
</script>

<div class="map-view">
  <div class="frame">
    {#if error}
      <div class="msg">Map unavailable.</div>
    {:else if manifest}
      <div class="stage">
        <img class="base" src={baseUrl()} alt="Mario Kart World map" />
        <!-- SP2 (territory) draws here, between the base and the icons -->
        <div class="territory" aria-hidden="true"></div>
        <div class="icons">
          {#each manifest.courses as c (c.slug)}
            <div class="hit" data-slug={c.slug} title={c.name} style={hitStyle(c.hit)}>
              <img class="spr" src={spriteUrl(c.slug)} alt={c.name}
                   draggable="false" style={spriteStyle(c.hit, c.spr)} />
            </div>
          {/each}
        </div>
        <!-- SP3 (hover popup) mounts here -->
        <div class="popups" aria-hidden="true"></div>
      </div>
    {:else}
      <div class="msg">Loading map…</div>
    {/if}
  </div>
</div>

<style>
  .map-view { padding: 16px; }
  .frame {
    position: relative; max-width: 1100px; margin: 0 auto;
    background: var(--feed-bg); border: 1px solid var(--bd);
    border-radius: var(--r); overflow: hidden;
  }
  .frame::after {
    content: ""; position: absolute; inset: 0; pointer-events: none;
    border-radius: var(--r); box-shadow: inset 0 0 60px 10px rgba(0,0,0,.45);
  }
  .stage { position: relative; width: 100%; }
  /* calm at rest: the whole map sits muted so SP2's territory colour can lead */
  .base { display: block; width: 100%; height: auto; filter: saturate(.82) brightness(.9); }
  .territory, .popups { position: absolute; inset: 0; pointer-events: none; }
  .icons { position: absolute; inset: 0; }
  .hit { position: absolute; cursor: pointer; }
  .spr {
    position: absolute; pointer-events: none; transform-origin: 50% 70%;
    filter: saturate(.78) brightness(.86);
    transition: transform .15s ease, filter .15s ease; will-change: transform;
  }
  .hit:hover { z-index: 50; }
  /* grow-dominant lift: the enlarged sprite always covers its own footprint */
  .hit:hover .spr {
    transform: scale(1.18) translateY(-4%);
    filter: drop-shadow(0 7px 9px rgba(0,0,0,.6)) brightness(1.08) saturate(1.05);
  }
  .msg { padding: 4rem; text-align: center; color: var(--tx-dim); }
  @media (max-width: 560px) { .map-view { padding: 8px; } }
</style>

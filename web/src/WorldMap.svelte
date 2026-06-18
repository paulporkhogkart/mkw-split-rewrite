<script>
  import { onMount, onDestroy, tick } from "svelte";
  import { baseUrl, manifestUrl, spriteUrl, hitStyle, spriteStyle } from "./lib/map.js";
  import CoursePopup from "./CoursePopup.svelte";
  import { fetchCourseView } from "./lib/courseData.js";
  import { API_BASE } from "./lib/api.js";

  let manifest = null;
  let error = false;

  // Hover popup (glance tooltip: open on icon enter, close on icon leave).
  let view = null, shown = false, popupEl, stageEl, popupStyle = "", closeTimer = 0, activeHit = null;

  async function openCourse(course, hitEl) {
    clearTimeout(closeTimer);
    activeHit = hitEl;
    const v = await fetchCourseView(API_BASE, course).catch(() => null);
    if (!v) return;
    view = v;
    await tick();                                   // CoursePopup renders -> measurable
    place(hitEl);
    requestAnimationFrame(() => (shown = true));    // class drives the fade/scale-in
  }
  function scheduleClose() { clearTimeout(closeTimer); closeTimer = setTimeout(() => (shown = false), 90); }

  function place(hitEl) {
    if (!stageEl || !popupEl) return;
    const fr = stageEl.getBoundingClientRect(), hr = hitEl.getBoundingClientRect();
    const cx = hr.left - fr.left + hr.width / 2, cy = hr.top - fr.top + hr.height / 2;
    const pw = popupEl.offsetWidth, ph = popupEl.offsetHeight, off = Math.max(hr.width, hr.height) * 0.55 + 8;
    // Project from the icon: pick the side the popup actually fits on; if neither fits,
    // the side with more room (then clamp). Avoids slamming centre courses to the edge.
    const fitsRight = cx + off + pw + 6 <= fr.width, fitsLeft = cx - off - pw - 6 >= 0;
    const right = fitsRight || (!fitsLeft && fr.width - cx >= cx);
    const fitsBelow = cy + off + ph + 6 <= fr.height, fitsAbove = cy - off - ph - 6 >= 0;
    const below = fitsBelow || (!fitsAbove && fr.height - cy >= cy);
    let left = right ? cx + off : cx - off - pw, top = below ? cy + off : cy - off - ph;
    left = Math.max(6, Math.min(left, fr.width - pw - 6));
    top = Math.max(6, Math.min(top, fr.height - ph - 6));
    popupStyle = `left:${left}px;top:${top}px;transform-origin:${right ? "left" : "right"} ${below ? "top" : "bottom"}`;
  }

  // Touch: a tap outside the open course's icon dismisses the popup.
  function onDocPointerDown(e) { if (shown && activeHit && !activeHit.contains(e.target)) shown = false; }
  onDestroy(() => { if (typeof document !== "undefined") document.removeEventListener("pointerdown", onDocPointerDown); });

  onMount(async () => {
    if (typeof document !== "undefined") document.addEventListener("pointerdown", onDocPointerDown);
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
      <div class="stage" bind:this={stageEl}>
        <img class="base" src={baseUrl()} alt="Mario Kart World map" />
        <!-- SP2 (territory) draws here, between the base and the icons -->
        <div class="territory" aria-hidden="true"></div>
        <div class="icons">
          {#each manifest.courses as c (c.slug)}
            <div class="hit" data-slug={c.slug} style={hitStyle(c.hit)}
                 on:mouseenter={(e) => openCourse(c, e.currentTarget)}
                 on:mouseleave={scheduleClose}
                 on:click={(e) => openCourse(c, e.currentTarget)}>
              <img class="shadow" src={spriteUrl(c.slug)} alt="" aria-hidden="true"
                   draggable="false" style={spriteStyle(c.hit, c.spr)} />
              <img class="spr" src={spriteUrl(c.slug)} alt={c.name}
                   draggable="false" style={spriteStyle(c.hit, c.spr)} />
            </div>
          {/each}
        </div>
        <div class="popups">
          <div class="popup" class:show={shown} bind:this={popupEl} style={popupStyle} aria-hidden={!shown}>
            <CoursePopup {view} />
          </div>
        </div>
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
  /* Calm at rest: the whole map sits muted so the hovered course (and SP2's territory) leads. */
  .base { display: block; width: 100%; height: auto; filter: saturate(.82) brightness(.9); }
  .territory, .popups { position: absolute; inset: 0; pointer-events: none; }
  .icons { position: absolute; inset: 0; }
  .hit { position: absolute; cursor: pointer; }
  .spr, .shadow {
    position: absolute; pointer-events: none; will-change: transform;
    transition: transform .18s ease, filter .18s ease, opacity .18s ease;
  }
  /* Icons are muted at rest too; hovering one brings it to full vivid colour (living-icon hover). */
  .spr { transform-origin: 50% 90%; filter: saturate(.78) brightness(.86); }
  /* The shadow is a live black silhouette of the course, sitting just below it. */
  .shadow {
    transform-origin: 50% 100%;
    filter: brightness(0);
    opacity: .42;
    transform: translateY(7%);
  }
  .hit:hover { z-index: 50; }
  /* On hover the course rises and its shadow drops + spreads + fades, so it reads as lifting. */
  .hit:hover .spr {
    transform: translateY(-12%) scale(1.13);
    filter: brightness(1.1) saturate(1.08);
  }
  .hit:hover .shadow {
    transform: translateY(11%) scale(1.06);
    opacity: .30;
  }
  /* The popup: kept in layout (opacity:0) so it can be measured for anchoring, faded/scaled
     in via the .show class only (never inline opacity — inline would override the class). */
  .popup {
    position: absolute; z-index: 80; pointer-events: none;
    opacity: 0; transform: scale(.92);
    transition: opacity .14s ease, transform .14s cubic-bezier(.2,.9,.3,1.2);
  }
  .popup.show { opacity: 1; transform: scale(1); }
  .msg { padding: 4rem; text-align: center; color: var(--tx-dim); }
  @media (max-width: 560px) { .map-view { padding: 8px; } }
</style>

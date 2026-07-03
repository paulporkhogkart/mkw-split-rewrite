<script>
  import { onMount, tick } from "svelte";
  import { viewFromPath } from "./lib/view.js";
  import Wordmark from "./lib/Wordmark.svelte";
  import WordmarkFire from "./lib/WordmarkFire.svelte";
  import wordmarkConfig from "./lib/wordmark.config.json";
  import CardWall from "./CardWall.svelte";
  import WorldMap from "./WorldMap.svelte";
  import HeatGraph from "./HeatGraph.svelte";
  import VersionPage from "./VersionPage.svelte";

  // The navbar wears a random player's figure + colour each load; hovering lights the on-fire
  // variant. The player's colour drives the whole nav accent (OFF tag + active-tab marker).
  const brandNames = Object.keys(wordmarkConfig.players);
  const brandPlayer = brandNames[Math.floor(Math.random() * brandNames.length)];
  const brandColor = wordmarkConfig.players[brandPlayer].color;
  let brandHot = false;

  let view = viewFromPath(typeof location !== "undefined" ? location.pathname : "/");
  let navEl;
  let mk = { left: 0, width: 0 };

  // Slide the red marker under the active tab (rhymes with the OFF tag).
  function updateMarker() {
    if (!navEl) return;
    const active = navEl.querySelector(".tab.on");
    if (!active) { mk = { ...mk, width: 0 }; return; }
    // The uppercase tabs carry trailing letter-spacing, which shifts the visible glyphs left of
    // the box centre; pull the marker left by half of it so it sits under the text, not the box.
    const ls = parseFloat(getComputedStyle(active).letterSpacing) || 0;
    mk = { left: active.offsetLeft + 10 - ls / 2, width: active.offsetWidth - 20 };
  }
  // Reposition after the .on class lands in the DOM (tab switch).
  $: view, tick().then(updateMarker);

  // Plain left-clicks on internal links switch views in-SPA (pushState, no reload). Modifier /
  // middle clicks fall through to a real navigation, which also works — the static server serves
  // the app shell for every route path.
  function navigate(e) {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const path = e.currentTarget.getAttribute("href");
    e.preventDefault();
    if (path !== location.pathname) history.pushState({}, "", path);
    view = viewFromPath(location.pathname);
  }

  onMount(() => {
    const sync = () => (view = viewFromPath(location.pathname));
    window.addEventListener("popstate", sync);
    window.addEventListener("resize", updateMarker);
    // Webfont metrics change offset widths — recompute once Inter has loaded.
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(updateMarker);
    updateMarker();
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("resize", updateMarker);
    };
  });
</script>

<header class="top" style="--brand-accent:{brandColor}">
  <a class="brand" href="/" aria-label="THE KART-OFF — home" on:click={navigate}
     on:mouseenter={() => (brandHot = true)} on:mouseleave={() => (brandHot = false)}>
    <WordmarkFire color={brandColor} active={brandHot} />
    <span class="wmwrap"><Wordmark size="22px" player={brandPlayer} fire={brandHot} /></span>
  </a>
  <nav class="nav" bind:this={navEl}>
    <a class="tab" class:on={view === "live"} href="/" on:click={navigate}>Live</a>
    <a class="tab" class:on={view === "turf"} href="/turf" on:click={navigate}>Turf</a>
    <span class="marker" style="left:{mk.left}px;width:{mk.width}px"></span>
  </nav>
</header>
<main>
  {#if view === "turf"}<WorldMap />
  {:else if view === "heat"}<HeatGraph />
  {:else if view === "version"}<VersionPage />
  {:else}<CardWall />{/if}
</main>

<style>
  .top{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:34px;
       height:50px;padding:0 24px;background:var(--bg);border-bottom:1px solid var(--bd);}
  .brand{position:relative;display:inline-flex;align-items:center;text-decoration:none;
         --wf-top:-11px;--wf-bottom:-5px;--wf-x:-14px;}
  .wmwrap{position:relative;z-index:1;}

  .nav{position:relative;display:flex;gap:2px;align-items:center;}
  .tab{font-family:'Inter',system-ui,sans-serif;font-weight:700;font-size:12px;letter-spacing:.15em;
       text-transform:uppercase;color:var(--tx-mut);text-decoration:none;padding:5px 9px;line-height:1;
       transition:color .18s ease;}
  .tab:hover{color:#f3f4f6;}
  .tab.on{color:#f3f4f6;}
  .marker{position:absolute;bottom:-2px;height:2.5px;background:var(--brand-accent,#ff4438);border-radius:2px;left:0;width:0;
          transition:left .26s cubic-bezier(.4,0,.2,1),width .26s cubic-bezier(.4,0,.2,1);}
</style>

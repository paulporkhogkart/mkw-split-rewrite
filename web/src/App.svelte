<script>
  import { presence } from "../../src/lib/stores.js";
  import CardWall from "./CardWall.svelte";
  $: vals = Object.values($presence);
  $: online = vals.filter((p) => p.online).length;
  $: racing = vals.filter((p) => p.online && p.screen === "RACING" && !p.final_time).length;
</script>

<header class="top">
  <div class="brand"><span class="a">the</span><span class="b">kartoff</span></div>
  <div class="live"><span class="dot"></span><b>{online}</b>&nbsp;online&nbsp;·&nbsp;<b>{racing}</b>&nbsp;racing</div>
</header>
<main><CardWall /></main>

<style>
  .top{position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;
       padding:13px 22px;background:var(--panel);border-bottom:1px solid var(--bd);}
  .brand{display:flex;align-items:baseline;gap:2px;font-size:16px;font-weight:700;letter-spacing:.01em;}
  .brand .a{color:var(--tx);} .brand .b{color:var(--accent);}
  .live{display:flex;align-items:center;gap:8px;font-size:10.5px;letter-spacing:.09em;
        color:var(--tx-mut);text-transform:uppercase;}
  .live .dot{width:7px;height:7px;border-radius:50%;background:var(--ok);position:relative;}
  .live .dot::after{content:"";position:absolute;inset:0;border-radius:50%;background:var(--ok);
                    animation:pulse 1.8s ease-out infinite;}
  @keyframes pulse{0%{transform:scale(1);opacity:.5;}100%{transform:scale(2.6);opacity:0;}}
  .live b{color:var(--tx);font-weight:600;}
</style>

<script lang="ts">
  import { onMount } from "svelte";
  import { loadConfig } from "./stores/config";
  import { loadStrategies } from "./stores/strategies";
  import { loadActiveConfig } from "./stores/activeConfig";
  import Nav from "./components/Nav.svelte";
  import Chart from "./pages/Chart.svelte";
  import Backtest from "./pages/Backtest.svelte";
  import SignalFeed from "./pages/SignalFeed.svelte";
  import Stats from "./pages/Stats.svelte";

  let route = $state(window.location.hash || "#/chart");

  onMount(() => {
    void loadConfig();
    // Load active config first so strategies get per-config star ratings
    void loadActiveConfig().then((cfg) => loadStrategies(cfg?.config_name));
    const onHash = () => {
      route = window.location.hash;
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  });
</script>

<Nav {route} />

{#if route === "#/chart"}
  <Chart />
{:else if route === "#/backtest"}
  <Backtest />
{:else if route === "#/signals"}
  <SignalFeed />
{:else if route === "#/stats"}
  <Stats />
{:else}
  <Chart />
{/if}

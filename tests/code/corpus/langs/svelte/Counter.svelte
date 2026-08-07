<script lang="ts">
  import { onMount } from "svelte";

  interface Props {
    start: number;
  }

  export function reset(): void {
    seed();
  }

  function seed(): void {
    count = 0;
  }

  let count = 0;
</script>

<button on:click={reset}>{count}</button>

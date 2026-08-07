<script context="module" lang="ts">
  export function panelClass(kind: string): string {
    return kind;
  }
</script>

<script lang="ts">
  import { panelClass } from "./Panel.svelte";

  function apply(): string {
    return panelClass("wide");
  }
</script>

<section class={apply()}><slot /></section>

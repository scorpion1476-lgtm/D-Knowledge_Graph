<template>
  <section><slot /></section>
</template>

<script lang="ts">
import { defineComponent } from "vue";

type PanelKind = "wide" | "narrow";

export function panelClass(kind: PanelKind): string {
  return kind;
}

export default defineComponent({
  name: "Panel",
});
</script>

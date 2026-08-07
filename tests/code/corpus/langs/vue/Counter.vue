<template>
  <button @click="bump">{{ count }}</button>
</template>

<script setup lang="ts">
import { ref } from "vue";

interface Props {
  start: number;
}

const count = ref(0);

function bump(): void {
  increment();
}

function increment(): void {
  count.value = count.value + 1;
}
</script>

<style scoped>
button { color: red; }
</style>

<script setup>
import { storeToRefs } from 'pinia'
import { useItineraryStore } from '../stores/itinerary'

const store = useItineraryStore()
const { ai } = storeToRefs(store)
</script>

<template>
  <div class="border border-[var(--line)] bg-[rgba(10,10,10,0.72)]">
    <div class="flex items-center justify-between border-b border-[var(--line)] px-3 py-2">
      <div class="mono text-[11px] uppercase tracking-[0.22em] text-[var(--dim)]">AI Answer</div>
    </div>
    <div class="mono px-3 py-3 text-[12px] leading-5 text-[var(--muted)] whitespace-pre-wrap">
      {{ ai.output || '生成后将在此显示 AI 的自然语言回答' }}
    </div>
  </div>
</template>


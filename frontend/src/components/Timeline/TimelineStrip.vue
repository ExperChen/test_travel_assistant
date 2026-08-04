<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useItineraryStore } from '../../stores/itinerary'
import { useHorizontalWheelScroll } from '../../composables/useHorizontalWheelScroll'
import TimelineNode from './TimelineNode.vue'

const store = useItineraryStore()
const { timelineItems } = storeToRefs(store)

const viewportRef = ref(null)
useHorizontalWheelScroll(viewportRef)

const hasItems = computed(() => timelineItems.value.length > 0)
</script>

<template>
  <section v-if="hasItems" class="border border-[var(--line)] bg-[rgba(10,10,10,0.6)]">
    <div class="flex items-center justify-between border-b border-[var(--line)] px-4 py-3">
      <div class="text-[11px] uppercase tracking-[0.24em] text-[var(--dim)]">Timeline</div>
      <div class="text-[11px] uppercase tracking-[0.22em] text-[rgba(244,244,245,0.45)]">Scroll or drag</div>
    </div>
    <div
      ref="viewportRef"
      class="overflow-x-auto no-scrollbar cursor-grab select-none active:cursor-grabbing"
      style="height: 190px"
    >
      <div class="relative inline-flex h-full items-stretch px-6" style="min-width: 100%">
        <div class="pointer-events-none absolute left-6 right-6 top-1/2 h-px -translate-y-1/2 bg-[var(--line)]"></div>
        <TimelineNode
          v-for="item in timelineItems"
          :key="item.id"
          :item="item"
          class="shrink-0"
        />
      </div>
    </div>
  </section>
</template>

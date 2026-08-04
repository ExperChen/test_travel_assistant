<script setup>
import { storeToRefs } from 'pinia'
import ItineraryForm from '../components/ItineraryForm.vue'
import TimelinePanel from '../components/Timeline/TimelinePanel.vue'
import TimelineStrip from '../components/Timeline/TimelineStrip.vue'
import DiscoOrb from '../components/DiscoOrb.vue'
import { useItineraryStore } from '../stores/itinerary'

const store = useItineraryStore()
const { ui } = storeToRefs(store)
</script>

<template>
  <div class="grid grid-cols-12 gap-6">
    <aside class="col-span-12 lg:col-span-4">
      <div class="flex h-full flex-col gap-4">
        <div class="w-full border border-[var(--line)] bg-[rgba(10,10,10,0.6)]">
          <div class="h-[150px] w-full">
            <DiscoOrb :active="ui.loading" />
          </div>
        </div>
        <ItineraryForm />
      </div>
    </aside>
    <section class="col-span-12 lg:col-span-8 flex flex-col gap-4">
      <TimelineStrip />
      <TimelinePanel />
    </section>
  </div>
</template>


<script setup>
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useItineraryStore } from '../../stores/itinerary'

const store = useItineraryStore()
const { ui, raw } = storeToRefs(store)

const hasData = computed(() => {
  const r = raw.value ?? {}
  return (r.flights?.length > 0) || (r.hotels?.length > 0) || (r.views?.length > 0)
})

const flights = computed(() => Array.isArray(raw.value?.flights) ? raw.value.flights : [])
const hotels = computed(() => Array.isArray(raw.value?.hotels) ? raw.value.hotels : [])
const views = computed(() => Array.isArray(raw.value?.views) ? raw.value.views : [])

function formatTime(isoString) {
  if (!isoString) return '--'
  try {
    const d = new Date(isoString)
    if (isNaN(d.getTime())) return isoString
    return d.toLocaleString('en-US', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false
    })
  } catch {
    return isoString
  }
}
</script>

<template>
  <section class="flex h-[800px] flex-col border border-[var(--line)] bg-[rgba(10,10,10,0.6)]">
    <div class="flex items-center justify-between border-b border-[var(--line)] px-4 py-3">
      <div class="text-[11px] uppercase tracking-[0.24em] text-[var(--dim)]">Itinerary Details</div>
      <div class="text-[11px] uppercase tracking-[0.22em] text-[rgba(244,244,245,0.45)]">
        <span v-if="ui.loading">RUNNING</span>
        <span v-else>READY</span>
      </div>
    </div>

    <div class="flex-1 overflow-y-auto no-scrollbar p-6">
      <div v-if="ui.loading" class="grid h-full place-items-center">
        <div class="text-[12px] uppercase tracking-[0.28em] text-[rgba(244,244,245,0.55)] animate-pulse">
          Generating Itinerary...
        </div>
      </div>

      <div v-else-if="!hasData" class="grid h-full place-items-center">
        <div class="max-w-[520px] text-center">
          <div class="text-[12px] uppercase tracking-[0.28em] text-[rgba(244,244,245,0.55)]">No Data</div>
          <div class="mt-2 text-[13px] text-[rgba(244,244,245,0.62)]">
            Generate an itinerary to view flight, hotel, and attraction details here.
          </div>
        </div>
      </div>

      <div v-else class="space-y-8">
        <!-- Flights Section -->
        <div v-if="flights.length > 0">
          <h3 class="text-[12px] uppercase tracking-[0.2em] text-[var(--chalk)] mb-4 border-b border-[var(--line)] pb-2">Flights</h3>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div v-for="(f, i) in flights" :key="'flight-'+i" class="border border-[var(--line)] bg-[rgba(10,10,10,0.4)] p-4 hover:border-[var(--line-strong)] transition-colors">
              <div class="flex justify-between items-start mb-2">
                <div class="text-[14px] font-medium text-[var(--chalk)]">{{ f.airline_company || f.name }}</div>
                <div class="text-[12px] font-mono text-[var(--dim)]">{{ f.code }}</div>
              </div>
              <div class="flex items-center gap-3 text-[13px] text-[var(--muted)] my-3">
                <div class="text-right">
                  <div class="font-mono text-[14px] text-[var(--chalk)]">{{ f.departure_airport }}</div>
                  <div class="text-[11px] mt-1">{{ formatTime(f.departure_date) }}</div>
                </div>
                <div class="flex-1 flex items-center">
                  <div class="h-[1px] bg-[var(--line)] w-full"></div>
                  <div class="mx-2 text-[10px]">✈</div>
                  <div class="h-[1px] bg-[var(--line)] w-full"></div>
                </div>
                <div class="text-left">
                  <div class="font-mono text-[14px] text-[var(--chalk)]">{{ f.arrival_airport }}</div>
                  <div class="text-[11px] mt-1">{{ formatTime(f.arrival_date) }}</div>
                </div>
              </div>
              <div class="flex justify-between items-end mt-4 pt-3 border-t border-[var(--line)]">
                <div class="text-[11px] text-[var(--dim)]">{{ f.luggage_limitation || 'No checked bag info' }}</div>
                <div class="text-[14px] font-mono text-[var(--chalk)]">{{ f.price > 0 ? `RM${f.price}` : 'Price TBD' }}</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Hotels Section -->
        <div v-if="hotels.length > 0">
          <h3 class="text-[12px] uppercase tracking-[0.2em] text-[var(--chalk)] mb-4 border-b border-[var(--line)] pb-2">Accommodation</h3>
          <div class="grid grid-cols-1 gap-4">
            <div v-for="(h, i) in hotels" :key="'hotel-'+i" class="border border-[var(--line)] bg-[rgba(10,10,10,0.4)] p-4 flex flex-col md:flex-row justify-between gap-4 hover:border-[var(--line-strong)] transition-colors">
              <div class="flex-1">
                <div class="flex items-center gap-3 mb-1">
                  <div class="text-[14px] font-medium text-[var(--chalk)]">{{ h.name }}</div>
                  <div v-if="h.rating" class="text-[11px] bg-[rgba(255,255,255,0.1)] px-1.5 py-0.5 rounded-sm">★ {{ h.rating }}</div>
                </div>
                <div class="text-[12px] text-[var(--dim)] mb-3 flex items-start gap-1.5">
                  <span class="mt-0.5">📍</span>
                  <span>{{ h.location || h.hotel_source || 'Address unknown' }}</span>
                </div>
                <div class="flex gap-4 text-[12px] text-[var(--muted)]">
                  <div><span class="text-[var(--dim)]">In:</span> {{ h.arrive_date }}</div>
                  <div><span class="text-[var(--dim)]">Out:</span> {{ h.leave_date }}</div>
                </div>
              </div>
              <div class="flex flex-col justify-between items-end min-w-[100px] border-t md:border-t-0 md:border-l border-[var(--line)] pt-3 md:pt-0 md:pl-4">
                <div class="text-[15px] font-mono text-[var(--chalk)] mb-2">{{ h.price > 0 ? `RM${h.price}` : 'Price TBD' }}</div>
                <a v-if="h.hotel_source && h.hotel_source.startsWith('http')" :href="h.hotel_source" target="_blank" class="text-[11px] uppercase tracking-wider text-[var(--dim)] hover:text-[var(--chalk)] border border-[var(--line)] px-2 py-1">View Details</a>
              </div>
            </div>
          </div>
        </div>

        <!-- Attractions Section -->
        <div v-if="views.length > 0">
          <h3 class="text-[12px] uppercase tracking-[0.2em] text-[var(--chalk)] mb-4 border-b border-[var(--line)] pb-2">Recommended Attractions</h3>
          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <div v-for="(v, i) in views" :key="'view-'+i" class="border border-[var(--line)] bg-[rgba(10,10,10,0.4)] overflow-hidden flex flex-col hover:border-[var(--line-strong)] transition-colors">
              <div v-if="v.image" class="h-32 w-full overflow-hidden border-b border-[var(--line)]">
                <img :src="v.image" :alt="v.name" class="w-full h-full object-cover opacity-80 hover:opacity-100 transition-opacity" />
              </div>
              <div class="p-4 flex-1 flex flex-col">
                <div class="text-[14px] font-medium text-[var(--chalk)] mb-1 leading-tight">{{ v.name }}</div>
                <div class="text-[11px] text-[var(--dim)] mb-3">{{ v.location }}</div>
                
                <p v-if="v.information" class="text-[12px] text-[var(--muted)] mb-4 line-clamp-3 leading-relaxed flex-1">
                  {{ v.information }}
                </p>
                
                <div class="grid grid-cols-2 gap-y-2 text-[11px] text-[var(--dim)] mt-auto pt-3 border-t border-[var(--line)]">
                  <div v-if="v.visit_duration" class="flex items-center gap-1.5 col-span-2">
                    <span>⏱</span> {{ v.visit_duration }}
                  </div>
                  <div v-if="v.open_time" class="flex items-center gap-1.5 col-span-2 line-clamp-1" :title="v.open_time">
                    <span>🕒</span> {{ v.open_time }}
                  </div>
                  <div class="flex items-center gap-1.5 col-span-2 mt-1">
                    <span>🎟</span> <span class="font-mono text-[var(--chalk)]">{{ v.price > 0 ? `RM${v.price}` : 'Free / TBD' }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  </section>
</template>


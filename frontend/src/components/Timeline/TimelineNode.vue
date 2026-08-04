<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  item: {
    type: Object,
    required: true,
  },
})

const rootRef = ref(null)
const open = ref(false)
const imageFailed = ref(false)

const isMarker = computed(() => props.item.type === 'marker')
const isCluster = computed(() => props.item.type === 'cluster')

const icon = computed(() => {
  if (props.item.type === 'flight') return '✈️'
  if (props.item.type === 'hotel') return '🏨'
  if (props.item.type === 'view') return ''
  if (props.item.type === 'cluster') return ''
  return ''
})

const viewImage = computed(() => {
  if (props.item.type !== 'view') return ''
  if (imageFailed.value) return ''
  return props.item?.meta?.original?.image || ''
})

const hotelName = computed(() => {
  if (props.item.type !== 'hotel') return ''
  return String(props.item?.meta?.original?.name || props.item?.title || '').trim()
})

function viewLocation(item) {
  return String(item?.meta?.original?.location || item?.subtitle || '').trim()
}

function hotelInfo(item) {
  const o = item?.meta?.original || {}
  const rating = o?.rating
  const price = o?.price
  const parts = []
  if (rating != null && rating !== '') parts.push(String(rating))
  if (price != null && price !== '') parts.push(String(price))
  return parts.join(' · ')
}
const close = () => {
  open.value = false
}

const toggle = () => {
  if (isMarker.value) return
  open.value = !open.value
}

const onDocDown = (e) => {
  const el = rootRef.value
  if (!el) return
  if (!open.value) return
  const path = typeof e.composedPath === 'function' ? e.composedPath() : []
  if (path.includes(el)) return
  close()
}

onMounted(() => {
  document.addEventListener('mousedown', onDocDown)
})

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocDown)
})

const popoverItems = computed(() => {
  if (props.item.type === 'cluster') return props.item?.meta?.items || []
  return [props.item]
})
</script>

<template>
  <div ref="rootRef" class="relative h-[190px] w-[120px]">
    <div v-if="!isMarker" class="absolute -top-1 left-0 text-[11px] tracking-[0.08em] text-[rgba(244,244,245,0.45)]">
      {{ item.timeLabel }}
    </div>

    <button
      type="button"
      class="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2"
      :class="[isMarker ? 'cursor-default' : 'cursor-pointer']"
      @click="toggle"
    >
      <div
        v-if="isMarker"
        class="mono border border-[var(--line)] bg-[rgba(10,10,10,0.78)] px-3 py-1 text-[11px] uppercase tracking-[0.26em] text-[var(--muted)]"
      >
        {{ item.title }}
      </div>

      <div v-else class="relative w-[100px]">
        <div
          class="relative h-[100px] w-[100px] overflow-hidden border border-[var(--line)] bg-[rgba(10,10,10,0.72)] hover:border-[var(--line-strong)]"
        >
          <div v-if="item.type === 'flight' || item.type === 'hotel'" class="grid h-full w-full place-items-center text-[38px]">
            <span>{{ icon }}</span>
          </div>

          <div v-else-if="item.type === 'view'" class="h-full w-full">
            <img
              v-if="viewImage"
              :src="viewImage"
              class="h-full w-full object-cover grayscale contrast-125 saturate-50"
              alt=""
              loading="lazy"
              referrerpolicy="no-referrer"
              @error="imageFailed = true"
            />
            <div v-else class="grid h-full w-full place-items-center text-[12px] text-[rgba(244,244,245,0.55)]">VIEW</div>
          </div>

          <div v-else-if="item.type === 'cluster'" class="grid h-full w-full place-items-center">
            <div class="relative h-[24px] w-[30px]">
              <div class="absolute left-0 top-0 h-[18px] w-[18px] border border-[var(--line)] bg-[rgba(10,10,10,0.7)]"></div>
              <div class="absolute left-[7px] top-[5px] h-[18px] w-[18px] border border-[var(--line-strong)] bg-[rgba(10,10,10,0.78)]"></div>
            </div>
            <div class="absolute -bottom-2 left-1/2 -translate-x-1/2 border border-[var(--line)] bg-[rgba(10,10,10,0.88)] px-1 py-[1px] text-[9px] tracking-[0.18em] text-[rgba(244,244,245,0.72)]">
              {{ item.meta?.items?.length || 0 }}
            </div>
          </div>
        </div>

        <div
          v-if="item.type === 'hotel' && hotelName"
          class="absolute left-1/2 top-full mt-2 w-[100px] -translate-x-1/2 border border-[var(--line)] bg-[rgba(10,10,10,0.88)] px-2 py-1 text-[11px] text-[rgba(244,244,245,0.78)]"
        >
          <div class="truncate">{{ hotelName }}</div>
        </div>
      </div>
    </button>

    <div
      v-if="open"
      class="absolute left-1/2 top-1/2 z-30 w-[280px] -translate-x-1/2 -translate-y-[120%] border border-[var(--line)] bg-[rgba(10,10,10,0.92)] p-3 text-left"
    >
      <div class="mono text-[11px] uppercase tracking-[0.22em] text-[rgba(244,244,245,0.55)]">
        {{ item.type }}
      </div>
      <div v-for="(p, idx) in popoverItems" :key="p.id + '_' + idx" class="mt-3 border-t border-[var(--line)] pt-3 first:mt-2 first:border-t-0 first:pt-0">
        <div class="text-[13px] tracking-[0.01em] text-[rgba(244,244,245,0.92)]">
          {{ p.title }}
        </div>
        <div v-if="p.subtitle && p.type !== 'view'" class="mt-1 text-[12px] text-[rgba(244,244,245,0.62)]">
          {{ p.subtitle }}
        </div>
        <div class="mt-2 text-[11px] text-[rgba(244,244,245,0.45)]">
          <template v-if="p.type === 'view'">
            <span>{{ viewLocation(p) || '--' }}</span>
          </template>
          <template v-else-if="p.type === 'hotel'">
            <span>{{ hotelInfo(p) || '--' }}</span>
          </template>
          <template v-else>
            <span>{{ p.startAt }}</span>
            <span v-if="p.endAt"> → {{ p.endAt }}</span>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>


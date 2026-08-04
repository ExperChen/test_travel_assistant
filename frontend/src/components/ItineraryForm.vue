<script setup>
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useItineraryStore } from '../stores/itinerary'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const store = useItineraryStore()
const { ui, ai } = storeToRefs(store)

const userInput = ref('')
const mustVisitInput = ref('')
const submitError = ref('')

const isValid = computed(() => userInput.value.trim().length > 0)
const parsedAiOutput = computed(() => {
  return ai.value.output ? DOMPurify.sanitize(marked.parse(ai.value.output)) : ''
})

const onGenerate = async () => {
  submitError.value = ''
  const mustVisitAttractions = mustVisitInput.value
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean)
  try {
    await store.generateItinerary({ input: userInput.value.trim(), mustVisitAttractions })
  } catch (e) {
    submitError.value = String(e?.message || ui.value.error || 'Request failed.')
  }
}
</script>

<template>
  <div class="flex h-full flex-col gap-4">
    <div class="border border-[var(--line)] bg-[rgba(10,10,10,0.68)] p-4">
      <div class="mb-3 text-[11px] uppercase tracking-[0.24em] text-[var(--dim)]">Dialog</div>
      <div class="grid grid-cols-1 gap-3">
        <label class="block">
          <div class="mb-1 text-[11px] uppercase tracking-[0.2em] text-[var(--dim)]">Ask AI</div>
          <textarea
            v-model="userInput"
            rows="4"
            class="w-full border border-[var(--line)] bg-transparent px-3 py-2 text-[13px] text-[var(--chalk)] outline-none focus:border-[var(--line-strong)]"
            placeholder="请输入你的旅行需求，例如：从吉隆坡到曼谷，帮我安排3天行程"
          ></textarea>
        </label>
        <label class="block">
          <div class="mb-1 text-[11px] uppercase tracking-[0.2em] text-[var(--dim)]">Must-Visit Attractions (optional)</div>
          <input
            v-model="mustVisitInput"
            type="text"
            class="w-full border border-[var(--line)] bg-transparent px-3 py-2 text-[13px] text-[var(--chalk)] outline-none focus:border-[var(--line-strong)]"
            placeholder="用逗号分隔，例如：景福宫, N首尔塔"
          />
        </label>
        <div class="pt-1">
          <button
            type="button"
            :disabled="!isValid || ui.loading"
            class="w-full border border-[var(--line)] px-3 py-3 text-[11px] uppercase tracking-[0.28em] text-[var(--muted)] hover:border-[var(--line-strong)] hover:text-[var(--chalk)] disabled:cursor-not-allowed disabled:opacity-40"
            @click="onGenerate"
          >
            <span v-if="ui.loading">Generating…</span>
            <span v-else>Generate Itinerary</span>
          </button>
        </div>
        <div v-if="submitError" class="border border-[var(--line)] bg-[rgba(10,10,10,0.6)] px-3 py-2 text-[12px] text-[rgba(244,244,245,0.82)]">
          {{ submitError }}
        </div>
      </div>
    </div>

    <div class="border border-[var(--line)] bg-[rgba(10,10,10,0.72)] flex flex-col h-[320px]">
      <div class="flex items-center justify-between border-b border-[var(--line)] px-3 py-2 shrink-0 bg-[rgba(10,10,10,0.9)] backdrop-blur">
        <div class="mono text-[11px] uppercase tracking-[0.22em] text-[var(--dim)]">AI Answer</div>
      </div>
      <div class="px-3 py-3 text-[13px] leading-6 text-[var(--muted)] prose prose-invert max-w-none flex-1 overflow-y-auto custom-scrollbar">
        <div v-if="parsedAiOutput" v-html="parsedAiOutput"></div>
        <div v-else class="mono text-[12px]">生成后将在此显示 AI 的自然语言回答</div>
      </div>
    </div>
  </div>
</template>


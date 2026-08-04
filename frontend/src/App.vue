<script setup>
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="min-h-screen bg-[var(--void)] text-[var(--chalk)]">
    <header class="sticky top-0 z-40 border-b border-[var(--line)] bg-[rgba(10,10,10,0.86)] backdrop-blur">
      <div class="mx-auto flex max-w-[1680px] items-center justify-between px-6 py-4">
        <div class="text-xs uppercase tracking-[0.28em] text-[var(--muted)]">AI Travel Assistant</div>
        <nav class="flex items-center gap-6 text-[11px] uppercase tracking-[0.24em] text-[var(--dim)]">
          <RouterLink
            to="/"
            class="border-b border-transparent pb-1 hover:text-[var(--chalk)]"
            active-class="border-[var(--line-strong)] text-[var(--chalk)]"
            >Builder</RouterLink
          >
        </nav>
      </div>
    </header>
    <main class="mx-auto max-w-[1680px] px-6 py-6">
      <Transition name="route-switch" mode="out-in">
        <RouterView />
      </Transition>
    </main>
  </div>
</template>

<style>
.route-switch-enter-active,
.route-switch-leave-active {
  transition:
    opacity 0.28s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.28s cubic-bezier(0.22, 1, 0.36, 1);
}
.route-switch-enter-from {
  opacity: 0;
  transform: translateY(6px) scale(0.98);
}
.route-switch-leave-to {
  opacity: 0;
  transform: translateY(-6px) scale(0.98);
}
</style>

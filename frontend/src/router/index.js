import { createRouter, createWebHistory } from 'vue-router'

const ItineraryBuilder = () => import('../pages/ItineraryBuilder.vue')

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'builder', component: ItineraryBuilder },
  ],
})


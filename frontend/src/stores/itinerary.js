import { defineStore } from 'pinia'
import { transformItinerary } from '../lib/transformItinerary'

const USE_MOCK = import.meta.env.VITE_USE_MOCK === '1'
const DEMO_SEED = import.meta.env.VITE_DEMO_SEED === '1'

const MOCK_RAW = {
  flights: [
    {
      name: 'AirAsia',
      code: 'AK 123',
      departure_airport: 'KUL',
      arrival_airport: 'BKK',
      departure_date: '2026-03-26T09:00:00+08:00',
      arrival_date: '2026-03-26T11:05:00+08:00',
    },
  ],
  hotels: [
    {
      name: 'Siam Boutique Hotel',
      location: 'Bangkok',
      arrive_date: '2026-03-26',
      leave_date: '2026-03-29',
      rating: 4.4,
      price: 'CNY 1200',
      map_source: 'https://www.google.com/maps/search/?api=1&query=Bangkok',
    },
  ],
  views: [
    {
      name: 'Grand Palace',
      location: 'Bangkok',
      arrival_time: '2026-03-27T10:00:00+07:00',
      departure_time: '2026-03-27T12:00:00+07:00',
      image:
        'https://images.unsplash.com/photo-1548013146-72479768bada?auto=format&fit=crop&w=1200&q=60',
      map_source: 'https://www.google.com/maps/search/?api=1&query=Grand%20Palace%20Bangkok',
    },
    {
      name: 'Chatuchak Market',
      location: 'Bangkok',
      arrival_time: '2026-03-28T15:00:00+07:00',
      departure_time: '2026-03-28T18:00:00+07:00',
      image:
        'https://images.unsplash.com/photo-1526481280695-3c687fd5432c?auto=format&fit=crop&w=1200&q=60',
      map_source: 'https://www.google.com/maps/search/?api=1&query=Chatuchak%20Market',
    },
  ],
}

function copyMockRaw() {
  return JSON.parse(JSON.stringify(MOCK_RAW))
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function makeId() {
  return `${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

function normalizeBaseUrl(baseUrl) {
  const raw = typeof baseUrl === 'string' ? baseUrl.trim() : ''
  if (!raw) return ''
  return raw.endsWith('/') ? raw.slice(0, -1) : raw
}

function humanizeError(status) {
  if (status === 400) return 'Request rejected: check inputs.'
  if (status === 401) return 'Unauthorized: check the app API key configuration.'
  if (status === 429) return 'Too many requests. Please wait and try again.'
  if (status === 502) return 'Upstream service error. Try again shortly.'
  if (status === 500) return 'Server error. Try again shortly.'
  return 'Request failed. Try again.'
}

export const useItineraryStore = defineStore('itinerary', {
  state: () => ({
    raw: DEMO_SEED ? copyMockRaw() : { flights: [], hotels: [], views: [] },
    request: {
      departure: '',
      destination: [],
      pax: 1,
      budget: { min: 1000, max: 5000, currency: 'MYR' },
      time: { start_date: '', end_date: '' },
      mustVisitAttractions: [],
    },
    ui: { loading: false, error: null },
    ai: { lines: [], running: false, output: '' },
  }),
  getters: {
    timelineItems(state) {
      return transformItinerary(state.raw)
    },
    timelineByDate() {
      const items = this.timelineItems
      const out = {}
      for (const item of items) {
        if (!out[item.dateKey]) out[item.dateKey] = []
        out[item.dateKey].push(item)
      }
      return out
    },
  },
  actions: {
    appendAiLine({ level = 'info', text }) {
      const line = { id: makeId(), ts: Date.now(), level, text: String(text ?? '') }
      this.ai.lines.push(line)
      if (this.ai.lines.length > 260) this.ai.lines.splice(0, this.ai.lines.length - 260)
    },
    startAi() {
      this.ai.running = true
    },
    stopAi() {
      this.ai.running = false
    },
    async generateItinerary(payload) {
      this.ui.loading = true
      this.ui.error = null
      this.raw = { flights: [], hotels: [], views: [] }
      this.ai.output = ''

      this.ai.lines = []
      this.startAi()
      this.appendAiLine({ level: 'system', text: '> System: initializing agent pipeline…' })
      this.appendAiLine({ level: 'agent', text: '> Agent: validating request body…' })

      const spinner = ['|', '/', '-', '\\']
      let spinIdx = 0
      const timer = setInterval(() => {
        if (!this.ai.running) return
        this.appendAiLine({ level: 'agent', text: `> Agent: working ${spinner[spinIdx++ % spinner.length]}` })
      }, 850)

      try {
        const baseUrl = normalizeBaseUrl(import.meta.env.VITE_API_BASE_URL)
        const url = `${baseUrl}/api/v1/agent/generate_itinerary`

        let body
        const userInput = typeof payload?.input === 'string' ? payload.input.trim() : ''
        if (userInput) {
          body = { input: userInput }
        } else {
          body = {
            departure: payload.departure,
            destination: payload.destination,
            pax: payload.pax,
            budget: payload.budget,
            time: payload.time,
          }
        }
        if (Array.isArray(payload.mustVisitAttractions) && payload.mustVisitAttractions.length > 0) {
          body.must_visit_attractions = payload.mustVisitAttractions
        }

        this.appendAiLine({ level: 'agent', text: `> Agent: POST ${url || '/api/v1/agent/generate_itinerary'}` })

        if (USE_MOCK) {
          this.appendAiLine({ level: 'system', text: '> System: mock mode enabled.' })
          await sleep(650)
          this.raw = copyMockRaw()
          this.appendAiLine({ level: 'system', text: '> Done: Itinerary generated (mock).' })
          return this.raw
        }

        const apiKey = import.meta.env.VITE_APP_API_KEY
        const headers = { 'Content-Type': 'application/json' }
        if (apiKey) headers['X-API-Key'] = apiKey

        const res = await fetch(url || '/api/v1/agent/generate_itinerary', {
          method: 'POST',
          headers,
          body: JSON.stringify(body),
        })

        let json = null
        try {
          json = await res.json()
        } catch {
          json = null
        }

        if (!res.ok) {
          const msg = json?.message || humanizeError(res.status)
          const err = new Error(msg)
          err.status = res.status
          throw err
        }

        if (json?.code !== 200) {
          const err = new Error(json?.message || 'Request failed.')
          err.status = res.status
          throw err
        }

        const data = json?.data ?? {}
        this.raw = {
          flights: Array.isArray(data.flights) ? data.flights : [],
          hotels: Array.isArray(data.hotels) ? data.hotels : [],
          views: Array.isArray(data.views) ? data.views : [],
        }
        this.ai.output = typeof data.output === 'string' ? data.output : ''

        this.appendAiLine({ level: 'system', text: '> Done: Itinerary generated.' })
        return this.raw
      } catch (e) {
        const status = e?.status
        const msg = String(e?.message || humanizeError(status))
        this.ui.error = msg
        this.appendAiLine({ level: 'error', text: `> Error: ${msg}` })
        throw e
      } finally {
        clearInterval(timer)
        this.stopAi()
        this.ui.loading = false
      }
    },
  },
})


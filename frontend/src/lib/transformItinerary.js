function fnv1a(input) {
  let hash = 0x811c9dc5
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i)
    hash = (hash * 0x01000193) >>> 0
  }
  return hash.toString(16).padStart(8, '0')
}

function safeText(value) {
  if (value == null) return ''
  return String(value)
}

function parseMs(isoLike) {
  if (!isoLike) return null
  const ms = Date.parse(isoLike)
  if (!Number.isFinite(ms)) return null
  return ms
}

function pad2(n) {
  return String(n).padStart(2, '0')
}

function formatDateKey(ms) {
  const d = new Date(ms)
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

function formatTimeLabel(ms) {
  const d = new Date(ms)
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

function markerTitleFromDateKey(dateKey) {
  if (dateKey === 'unknown') return 'UNKNOWN'
  const [y, m, d] = dateKey.split('-')
  const monthIdx = Number(m) - 1
  const month = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'][
    Math.min(11, Math.max(0, monthIdx))
  ]
  return `${month} ${Number(d)}`
}

const TYPE_ORDER = {
  marker: 0,
  flight: 1,
  hotel: 2,
  view: 3,
  cluster: 4,
}

function makeBaseItem({ type, startAt, endAt, title, subtitle, meta }) {
  const ms = parseMs(startAt)
  const effectiveMs = ms ?? Date.parse('1970-01-01T00:00:00.000Z')
  const dateKey = ms == null ? 'unknown' : formatDateKey(effectiveMs)
  const timeLabel = ms == null ? '--:--' : formatTimeLabel(effectiveMs)
  const stable = `${type}|${safeText(startAt)}|${safeText(endAt)}|${safeText(title)}|${safeText(subtitle)}`
  return {
    id: `${type}_${fnv1a(stable)}`,
    type,
    startAt: ms == null ? new Date(effectiveMs).toISOString() : new Date(ms).toISOString(),
    endAt: endAt ? new Date(parseMs(endAt) ?? effectiveMs).toISOString() : undefined,
    dateKey,
    timeLabel,
    title: safeText(title),
    subtitle: subtitle ? safeText(subtitle) : '',
    meta: meta ?? {},
  }
}

function normalizeRaw(raw) {
  const flights = Array.isArray(raw?.flights) ? raw.flights : []
  const hotels = Array.isArray(raw?.hotels) ? raw.hotels : []
  const views = Array.isArray(raw?.views) ? raw.views : []
  return { flights, hotels, views }
}

function normalizeItems(raw) {
  const { flights, hotels, views } = normalizeRaw(raw)

  const flightItems = flights.map((f) =>
    makeBaseItem({
      type: 'flight',
      startAt: f?.departure_date,
      endAt: f?.arrival_date,
      title: `${safeText(f?.name)} ${safeText(f?.code)}`.trim() || safeText(f?.code) || 'Flight',
      subtitle: `${safeText(f?.departure_airport)} → ${safeText(f?.arrival_airport)}`.trim(),
      meta: { original: f },
    }),
  )

  const hotelItems = hotels.flatMap((h) => {
    const arrive = safeText(h?.arrive_date)
    const leave = safeText(h?.leave_date)
    const baseMeta = { original: h }

    return [
      makeBaseItem({
        type: 'hotel',
        startAt: arrive ? `${arrive}T15:00:00` : null,
        endAt: undefined,
        title: safeText(h?.name) || 'Hotel',
        subtitle: safeText(h?.location),
        meta: { ...baseMeta, kind: 'checkin' },
      }),
      makeBaseItem({
        type: 'hotel',
        startAt: leave ? `${leave}T11:00:00` : null,
        endAt: undefined,
        title: safeText(h?.name) || 'Hotel',
        subtitle: safeText(h?.location),
        meta: { ...baseMeta, kind: 'checkout' },
      }),
    ]
  })

  const viewItems = views.map((v) =>
    makeBaseItem({
      type: 'view',
      startAt: v?.arrival_time,
      endAt: v?.departure_time,
      title: safeText(v?.name) || 'View',
      subtitle: safeText(v?.location),
      meta: { original: v },
    }),
  )

  return [...flightItems, ...hotelItems, ...viewItems]
}

function sortItems(items) {
  return [...items].sort((a, b) => {
    const ams = a.dateKey === 'unknown' ? Number.POSITIVE_INFINITY : parseMs(a.startAt) ?? Number.POSITIVE_INFINITY
    const bms = b.dateKey === 'unknown' ? Number.POSITIVE_INFINITY : parseMs(b.startAt) ?? Number.POSITIVE_INFINITY
    if (ams !== bms) return ams - bms
    const ao = TYPE_ORDER[a.type] ?? 99
    const bo = TYPE_ORDER[b.type] ?? 99
    if (ao !== bo) return ao - bo
    return a.id.localeCompare(b.id)
  })
}

function consolidateClusters(items) {
  const out = []
  for (let i = 0; i < items.length; i++) {
    const cur = items[i]
    if (cur.type === 'marker' || cur.dateKey === 'unknown') {
      out.push(cur)
      continue
    }

    const curMs = parseMs(cur.startAt)
    if (curMs == null) {
      out.push(cur)
      continue
    }

    const bucket = [cur]
    let j = i + 1
    while (j < items.length) {
      const nxt = items[j]
      if (nxt.type === 'marker') break
      if (nxt.dateKey !== cur.dateKey) break
      if (nxt.dateKey === 'unknown') break
      const nxtMs = parseMs(nxt.startAt)
      if (nxtMs == null) break
      if (Math.abs(nxtMs - curMs) >= 60_000) break
      bucket.push(nxt)
      j++
    }

    if (bucket.length === 1) {
      out.push(cur)
      continue
    }

    const stable = bucket.map((x) => x.id).join('|')
    out.push(
      makeBaseItem({
        type: 'cluster',
        startAt: bucket[0].startAt,
        endAt: bucket[bucket.length - 1].endAt,
        title: `${bucket.length} events`,
        subtitle: '',
        meta: { items: bucket },
      }),
    )
    i = j - 1
  }
  return out
}

export function transformItinerary(raw) {
  const normalized = normalizeItems(raw)
  const sorted = sortItems(normalized)
  const clustered = consolidateClusters(sorted)

  const groups = new Map()
  for (const item of clustered) {
    if (!groups.has(item.dateKey)) groups.set(item.dateKey, [])
    groups.get(item.dateKey).push(item)
  }

  const result = []
  for (const [dateKey, groupItems] of groups) {
    const markerMs = parseMs(groupItems[0]?.startAt) ?? Date.parse('1970-01-01T00:00:00.000Z')
    const marker = makeBaseItem({
      type: 'marker',
      startAt: new Date(markerMs).toISOString(),
      endAt: undefined,
      title: markerTitleFromDateKey(dateKey),
      subtitle: '',
      meta: { dateKey },
    })
    result.push(marker, ...groupItems)
  }

  return sortItems(result)
}


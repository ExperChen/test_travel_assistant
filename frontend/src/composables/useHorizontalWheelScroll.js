import { onBeforeUnmount, onMounted } from 'vue'

export function useHorizontalWheelScroll(viewportRef) {
  let raf = 0
  let currentTarget = 0
  let dragActive = false
  let dragMoved = false
  let dragStartX = 0
  let dragStartScrollLeft = 0
  let suppressClick = false

  const animate = () => {
    raf = 0
    const el = viewportRef.value
    if (!el) return
    const cur = el.scrollLeft
    const next = cur + (currentTarget - cur) * 0.22
    if (Math.abs(currentTarget - cur) < 0.5) {
      el.scrollLeft = currentTarget
      return
    }
    el.scrollLeft = next
    raf = requestAnimationFrame(animate)
  }

  const setTarget = (nextTarget) => {
    const el = viewportRef.value
    if (!el) return
    const max = Math.max(0, el.scrollWidth - el.clientWidth)
    currentTarget = Math.min(max, Math.max(0, nextTarget))
    if (!raf) raf = requestAnimationFrame(animate)
  }

  const onWheel = (e) => {
    const el = viewportRef.value
    if (!el) return
    const dx = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY
    if (dx === 0) return
    e.preventDefault()
    setTarget((raf ? currentTarget : el.scrollLeft) + dx)
  }

  const scrollByStep = (step) => {
    const el = viewportRef.value
    if (!el) return
    setTarget((raf ? currentTarget : el.scrollLeft) + step)
  }

  const onPointerDown = (e) => {
    const el = viewportRef.value
    if (!el) return
    if (e.button != null && e.button !== 0) return

    const t = e.target
    if (t && typeof t.closest === 'function') {
      const interactive = t.closest('button, a, input, textarea, select, [role="button"]')
      if (interactive && interactive !== el) return
    }

    dragActive = true
    dragMoved = false
    dragStartX = e.clientX
    dragStartScrollLeft = el.scrollLeft

    if (typeof el.setPointerCapture === 'function' && e.pointerId != null) {
      try {
        el.setPointerCapture(e.pointerId)
      } catch {}
    }
  }

  const onPointerMove = (e) => {
    const el = viewportRef.value
    if (!el) return
    if (!dragActive) return

    const dx = e.clientX - dragStartX
    if (!dragMoved && Math.abs(dx) > 4) {
      dragMoved = true
      suppressClick = true
    }

    if (!dragMoved) return
    e.preventDefault()
    setTarget(dragStartScrollLeft - dx)
  }

  const endDrag = () => {
    dragActive = false
    if (suppressClick) {
      setTimeout(() => {
        suppressClick = false
      }, 0)
    }
  }

  const onPointerUp = () => endDrag()
  const onPointerCancel = () => endDrag()

  const onClickCapture = (e) => {
    if (!suppressClick) return
    e.preventDefault()
    e.stopPropagation()
  }

  onMounted(() => {
    const el = viewportRef.value
    if (!el) return
    currentTarget = el.scrollLeft
    el.addEventListener('wheel', onWheel, { passive: false })
    el.addEventListener('pointerdown', onPointerDown)
    el.addEventListener('pointermove', onPointerMove, { passive: false })
    el.addEventListener('pointerup', onPointerUp)
    el.addEventListener('pointercancel', onPointerCancel)
    el.addEventListener('click', onClickCapture, true)
  })

  onBeforeUnmount(() => {
    const el = viewportRef.value
    if (el) el.removeEventListener('wheel', onWheel)
    if (el) el.removeEventListener('pointerdown', onPointerDown)
    if (el) el.removeEventListener('pointermove', onPointerMove)
    if (el) el.removeEventListener('pointerup', onPointerUp)
    if (el) el.removeEventListener('pointercancel', onPointerCancel)
    if (el) el.removeEventListener('click', onClickCapture, true)
    if (raf) cancelAnimationFrame(raf)
  })

  return { scrollByStep }
}


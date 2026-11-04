// Reorder rows on a touch screen: press a row's grip (.m-grip) and drag it.
// The other rows slide out of the way while it moves, and the page scrolls
// when the finger nears the top or bottom of the visible band (the part not
// covered by fixed bars). Desktop uses sortable.js, built on native drag and
// drop, which phones do not support well.

const EDGE = 72; // px from the band's edge where scrolling starts
const MAX_SPEED = 900; // px per second at the very edge
const SETTLE_MS = 160;

// onMove(keysInNewOrder, movedKey) runs after a drop that changed the order.
// bounds() returns { top, bottom } of the visible band in viewport pixels.
export function makeTouchSortable(container, itemSelector, onMove, bounds = () => ({ top: 0, bottom: window.innerHeight })) {
  let drag = null;

  function place() {
    const { row, rows, from, centres, heights } = drag;
    const dy = drag.pointerY + window.scrollY - drag.startY;
    row.style.transform = `translateY(${dy}px)`;
    const centre = centres[from] + dy;
    let to = 0;
    rows.forEach((r, i) => { if (i !== from && centres[i] < centre) to += 1; });
    rows.forEach((r, i) => {
      if (i === from) return;
      const shift = i > from && i <= to ? -heights[from] : i < from && i >= to ? heights[from] : 0;
      r.style.transform = shift ? `translateY(${shift}px)` : "";
    });
    drag.to = to;
  }

  function tick(now) {
    drag.frame = requestAnimationFrame(tick);
    const seconds = Math.min((now - drag.last) / 1000, 0.25);
    drag.last = now;
    const { top, bottom } = bounds();
    const y = drag.pointerY;
    let speed = 0;
    if (y < top + EDGE) speed = -MAX_SPEED * Math.min(1, (top + EDGE - y) / EDGE);
    else if (y > bottom - EDGE) speed = MAX_SPEED * Math.min(1, (y - bottom + EDGE) / EDGE);
    drag.carry = speed ? drag.carry + speed * seconds : 0;
    const step = Math.trunc(drag.carry);
    if (step) {
      drag.carry -= step;
      window.scrollBy(0, step);
    }
    place();
  }

  container.addEventListener("pointerdown", (event) => {
    const grip = event.target.closest && event.target.closest(".m-grip");
    const row = grip && grip.closest(itemSelector);
    if (!row || drag || (event.pointerType === "mouse" && event.button !== 0)) return;
    event.preventDefault();
    grip.setPointerCapture(event.pointerId);
    const rows = Array.from(container.querySelectorAll(itemSelector));
    const rects = rows.map((r) => r.getBoundingClientRect());
    drag = {
      id: event.pointerId,
      row,
      rows,
      from: rows.indexOf(row),
      to: rows.indexOf(row),
      startY: event.clientY + window.scrollY,
      pointerY: event.clientY,
      // Document-space centres and heights, measured once before anything moves.
      centres: rects.map((b) => b.top + window.scrollY + b.height / 2),
      heights: rects.map((b) => b.height),
      frame: 0,
      last: performance.now(),
      carry: 0,
    };
    row.classList.add("is-lifted");
    for (const r of rows) if (r !== row) r.classList.add("is-shifting");
    if (navigator.vibrate) navigator.vibrate(8);
    drag.frame = requestAnimationFrame(tick);
  });

  container.addEventListener("pointermove", (event) => {
    if (drag && event.pointerId === drag.id) drag.pointerY = event.clientY;
  });

  const drop = (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    cancelAnimationFrame(drag.frame);
    const { row, rows, from, to, heights } = drag;
    drag = null;
    const commit = event.type === "pointerup" && to !== from;
    // Glide into the slot it will occupy, then hand over the new order.
    let slot = 0;
    if (commit && to > from) for (let i = from + 1; i <= to; i++) slot += heights[i];
    if (commit && to < from) for (let i = to; i < from; i++) slot -= heights[i];
    row.classList.add("is-settling");
    row.style.transform = slot ? `translateY(${slot}px)` : "";
    if (!commit) for (const r of rows) if (r !== row) r.style.transform = "";
    setTimeout(() => {
      for (const r of rows) {
        r.style.transform = "";
        r.classList.remove("is-lifted", "is-shifting", "is-settling");
      }
      if (!commit) return;
      const keys = rows.map((r) => r.dataset.key);
      const [moved] = keys.splice(from, 1);
      keys.splice(to, 0, moved);
      onMove(keys, moved);
    }, SETTLE_MS);
  };
  container.addEventListener("pointerup", drop);
  container.addEventListener("pointercancel", drop);
}

// Drag a bottom sheet down by its header to dismiss it, as native sheets do.
// A short, slow drag springs back; a long or quick one dismisses.

const SLOP = 6; // px a press may wander before it counts as a drag, so taps still click
const DISTANCE = 90; // px
const SPEED = 0.6; // px per ms, for a flick

export function swipeToDismiss(sheet, handle, onDismiss) {
  let drag = null;

  handle.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    drag = { id: event.pointerId, y: event.clientY, lastY: event.clientY, lastT: event.timeStamp, speed: 0, moving: false };
  });

  handle.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const dy = event.clientY - drag.y;
    if (!drag.moving) {
      if (dy < SLOP) return;
      drag.moving = true;
      // Capture only now: capturing on pointerdown would steal the click
      // from the close button in the same header.
      handle.setPointerCapture(event.pointerId);
      sheet.classList.add("is-dragged");
    }
    const dt = event.timeStamp - drag.lastT;
    if (dt > 0) drag.speed = (event.clientY - drag.lastY) / dt;
    drag.lastY = event.clientY;
    drag.lastT = event.timeStamp;
    sheet.style.transform = `translateY(${Math.max(0, dy - SLOP)}px)`;
  });

  const release = (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    const { moving, speed } = drag;
    const dy = event.clientY - drag.y;
    drag = null;
    if (!moving) return;
    sheet.classList.remove("is-dragged");
    if (event.type === "pointerup" && (dy > DISTANCE || speed > SPEED)) {
      sheet.classList.add("is-dismissed");
      setTimeout(onDismiss, 180);
    } else {
      sheet.style.transform = "";
    }
  };
  handle.addEventListener("pointerup", release);
  handle.addEventListener("pointercancel", release);
}

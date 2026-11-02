// Reorder rows by dragging a handle, or with the arrow keys while the handle
// has focus. The handle is a span: Firefox will not start a drag from a button.

import { h, icon } from "./dom.js";

export function dragHandle(name, enabled) {
  return h("span", {
    class: ["drag-handle", !enabled && "is-disabled"],
    draggable: enabled ? "true" : "false",
    tabindex: enabled ? "0" : "-1",
    role: "button",
    "aria-label": `调整 ${name} 的顺序`,
    "aria-disabled": enabled ? null : "true",
    title: enabled ? "拖动调整顺序；也可以选中后按上下方向键" : "清空搜索后才能调整顺序",
  }, icon("grip"));
}

// container: the element holding the rows; rows carry data-key.
// onMove(keysInNewOrder, movedKey) is called after a drop or an arrow key.
export function makeSortable(container, itemSelector, onMove) {
  let dragged = null;
  const rows = () => Array.from(container.querySelectorAll(itemSelector));
  const keyOf = (row) => row.dataset.key;
  const clearMarks = () => {
    for (const row of container.querySelectorAll(".drop-before, .drop-after")) row.classList.remove("drop-before", "drop-after");
  };
  const end = () => {
    clearMarks();
    if (dragged) dragged.classList.remove("is-dragging");
    dragged = null;
  };

  container.addEventListener("dragstart", (event) => {
    const handle = event.target.closest && event.target.closest(".drag-handle");
    const row = handle && handle.closest(itemSelector);
    if (!row || handle.classList.contains("is-disabled")) {
      event.preventDefault();
      return;
    }
    dragged = row;
    row.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", keyOf(row));
    event.dataTransfer.setDragImage(row, 24, 18);
  });

  container.addEventListener("dragover", (event) => {
    if (!dragged) return;
    const over = event.target.closest && event.target.closest(itemSelector);
    if (!over) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    clearMarks();
    if (over === dragged) return;
    const rect = over.getBoundingClientRect();
    over.classList.add(event.clientY < rect.top + rect.height / 2 ? "drop-before" : "drop-after");
  });

  container.addEventListener("drop", (event) => {
    if (!dragged) return;
    event.preventDefault();
    const target = container.querySelector(".drop-before, .drop-after");
    if (target) {
      const moved = keyOf(dragged);
      const keys = rows().map(keyOf).filter((key) => key !== moved);
      let index = keys.indexOf(keyOf(target));
      if (target.classList.contains("drop-after")) index += 1;
      keys.splice(index, 0, moved);
      onMove(keys, moved);
    }
    end();
  });

  container.addEventListener("dragend", end);

  container.addEventListener("keydown", (event) => {
    const handle = event.target;
    if (!handle.classList || !handle.classList.contains("drag-handle") || handle.classList.contains("is-disabled")) return;
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const all = rows();
    const from = all.indexOf(handle.closest(itemSelector));
    const to = event.key === "ArrowUp" ? from - 1 : from + 1;
    if (from < 0 || to < 0 || to >= all.length) return;
    const keys = all.map(keyOf);
    [keys[from], keys[to]] = [keys[to], keys[from]];
    onMove(keys, keys[to]);
  });
}

// Put keyboard focus back on a row's handle after the list was redrawn.
export function focusHandle(container, key) {
  for (const row of container.querySelectorAll("[data-key]")) {
    if (row.dataset.key === key) {
      const handle = row.querySelector(".drag-handle");
      if (handle) handle.focus();
      return;
    }
  }
}

// Building blocks for the phone layout: list rows that open a sheet with the
// details, the sheets themselves, and the switch into reorder mode.

import { h, icon, modal } from "./dom.js";

export function list(className) {
  return h("ul", { class: ["m-list", className] });
}

// One entry in a list. A tap anywhere on it runs onOpen, except on `quick`,
// which holds one shortcut such as copying the value. The grip only shows,
// and only works, while the list is in reorder mode.
export function row({ key, label, head, lines = [], quick, onOpen, className }) {
  return h("li", { class: ["m-row", className], dataset: key ? { key } : null },
    h("span", { class: "m-grip", "aria-hidden": "true" }, icon("grip")),
    h("div", { class: "m-row-body" }, h("div", { class: "m-row-head" }, head), lines),
    quick ? h("div", { class: "m-row-quick" }, quick) : null,
    onOpen ? h("button", { class: "m-row-open", type: "button", "aria-label": label, onclick: onOpen }) : null);
}

export function sub(text, className) {
  return text ? h("p", { class: ["m-row-sub", className] }, text) : null;
}

export function meta(...parts) {
  const shown = parts.filter(Boolean);
  return shown.length ? h("p", { class: "m-row-meta" }, shown.join(" · ")) : null;
}

// A bottom sheet showing one item; `build(close)` returns its contents.
export function detailSheet({ title, heading, build }) {
  return modal({ title, heading, sheet: true, build: (close) => h("div", { class: "modal-body m-detail" }, build(close)) });
}

export function detail(label, ...content) {
  return h("div", { class: "m-field" }, h("span", { class: "m-field-label" }, label), h("div", { class: "m-field-value" }, content));
}

export function actions(...buttons) {
  return h("div", { class: "m-actions" }, buttons);
}

// A sheet button that closes the sheet before running its action, which may
// open a dialog of its own.
export function sheetButton(close, { label, iconName, kind, disabled, action }) {
  return h("button", { class: ["button", kind && `button-${kind}`], type: "button", disabled,
    onclick: () => { close(); action(); } }, iconName ? icon(iconName) : null, label);
}

// The toolbar switch between browsing and reordering a list.
export function sortToggle(onChange) {
  const button = h("button", { class: "button button-quiet m-sort-toggle", type: "button", "aria-pressed": "false" });
  const show = (on) => {
    button.setAttribute("aria-pressed", String(on));
    button.replaceChildren(...(on ? [icon("check"), "完成"] : [icon("sort"), "排序"]));
  };
  button.addEventListener("click", () => {
    const on = button.getAttribute("aria-pressed") !== "true";
    show(on);
    onChange(on);
  });
  show(false);
  return button;
}

// The part of the viewport not covered by the fixed app bar and tab bar.
export function visibleBand() {
  const bar = document.querySelector(".appbar");
  const tabs = document.querySelector(".tabbar");
  return {
    top: bar ? bar.getBoundingClientRect().bottom : 0,
    bottom: tabs ? tabs.getBoundingClientRect().top : window.innerHeight,
  };
}

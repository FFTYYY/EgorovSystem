// The page shape shared by variables and functions: a heading with a "new"
// button, a search box, the items and an empty state. On desktop the items
// are rows with drag handles; on phones they are tappable rows that open a
// sheet, with a reorder mode instead of drag handles.

import { clear, h, icon, toast } from "../dom.js";
import { isMobile } from "../layout.js";
import * as m from "../mobile.js";
import { focusHandle, makeSortable } from "../sortable.js";
import { itemsOf, reorder } from "../state.js";
import { makeTouchSortable } from "../touchsort.js";

let slashHandler = null;

function bindSearchShortcut(input) {
  if (slashHandler) document.removeEventListener("keydown", slashHandler);
  slashHandler = (event) => {
    if (!input.isConnected) return;
    const tag = document.activeElement && document.activeElement.tagName;
    if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(tag) && !document.querySelector("dialog[open]")) {
      event.preventDefault();
      input.focus();
    }
  };
  document.addEventListener("keydown", slashHandler);
}

export function searchBox(placeholder, onInput) {
  const input = h("input", { type: "search", class: "search-input", placeholder, "aria-label": placeholder,
    spellcheck: "false", autocomplete: "off" });
  input.addEventListener("input", () => onInput(input.value.trim().toLowerCase()));
  bindSearchShortcut(input);
  return { input, node: h("label", { class: "search" }, icon("search"), input, h("kbd", { title: "按 / 搜索" }, "/")) };
}

// spec:
//   kind, title, lead, newLabel, placeholder, noMatch
//   matches(item, query)      does the item match the lowercased search text
//   create(changed)           open the "new" dialog
//   emptyState()              node shown when there are no items at all
//   desktop: { container() -> { node, body }, selector, row(item, { sortable, changed }) }
//   phone:   { row(item, { changed }) }   rows must carry data-key
// `changed()` redraws the list and the navigation counts after an edit.
export function renderCatalog(page, ctx, spec) {
  const mobile = isMobile();
  let filter = "";
  const changed = () => { draw(); ctx.refreshNav(); };
  const { node, body } = mobile ? (() => { const ul = m.list(); return { node: ul, body: ul }; })() : spec.desktop.container();
  const count = h("span", { class: "toolbar-count" });
  const empty = h("div", { class: "empty" });

  function draw() {
    const all = itemsOf(spec.kind);
    const shown = all.filter((item) => !filter || spec.matches(item, filter));
    clear(body).append(...shown.map((item) => (mobile
      ? spec.phone.row(item, { changed })
      : spec.desktop.row(item, { sortable: !filter, changed }))));
    count.textContent = filter ? `${shown.length} / ${all.length} 个` : `${all.length} 个`;
    node.hidden = shown.length === 0;
    empty.hidden = shown.length !== 0;
    clear(empty).append(all.length ? h("p", null, spec.noMatch) : spec.emptyState());
  }

  const onMove = (names, moved) => {
    reorder(spec.kind, names).catch((err) => { toast(err.message, "error"); draw(); });
    draw();
    if (!mobile) focusHandle(body, moved);
  };

  const search = searchBox(spec.placeholder, (value) => { filter = value; draw(); });
  const toolbar = h("div", { class: "toolbar" }, search.node);
  if (mobile) {
    makeTouchSortable(body, ".m-row", onMove, m.visibleBand);
    // Reordering needs the whole list, so it clears the search first.
    toolbar.append(m.sortToggle((on) => {
      if (on && filter) {
        search.input.value = "";
        filter = "";
        draw();
      }
      search.input.disabled = on;
      body.classList.toggle("is-sorting", on);
    }));
  } else {
    makeSortable(body, spec.desktop.selector, onMove);
  }
  toolbar.append(count);

  page.append(
    h("header", { class: "page-head" },
      h("div", null, h("h1", null, spec.title), h("p", { class: "page-lead" }, spec.lead)),
      h("button", { class: "button button-primary", type: "button", onclick: () => spec.create(changed) },
        icon("plus"), spec.newLabel)),
    toolbar, node, empty);
  draw();
}

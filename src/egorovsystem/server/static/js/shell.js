// The frame around every page. Wide screens get a sidebar; phones get an app
// bar on top and a tab bar at the bottom, with the pages that have no tab of
// their own under "更多".

import { clear, h, icon, mascot, modal } from "./dom.js";
import { install, installMode } from "./pwa.js";
import { session } from "./state.js";

// routes: [{ path, label, icon, tab, count }]; returns the page element and
// hooks to mark the current route and refresh the item counts.
export function buildShell(root, { routes, mobile, onLock, onSignOut }) {
  return (mobile ? phoneShell : desktopShell)(root, { routes, onLock, onSignOut });
}

function markCurrent(links, path) {
  for (const [key, link] of links) {
    if (key === path) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

function desktopShell(root, { routes, onLock, onSignOut }) {
  const links = new Map();
  const nav = h("nav", { class: "toc", "aria-label": "目录" },
    routes.map((route) => {
      const link = h("a", { class: "toc-item", href: `#/${route.path}` },
        h("span", { class: "toc-label" }, route.label),
        route.count && h("span", { class: "toc-leader", "aria-hidden": "true" }),
        route.count && h("span", { class: "toc-count" }, ""));
      links.set(route.path, link);
      return link;
    }));
  const page = h("main", { class: "page", id: "page", tabindex: "-1" });
  clear(root).append(h("div", { class: "shell" },
    h("aside", { class: "sidebar" },
      h("a", { class: "brand", href: "#/overview" }, mascot(42), h("span", { class: "brand-name" }, "Egorov")),
      nav,
      h("div", { class: "sidebar-foot" },
        h("p", { class: "who" }, h("span", { class: "who-label" }, "管理员"), h("span", { class: "who-name" }, session.username)),
        h("div", { class: "sidebar-actions" },
          h("button", { class: "button button-quiet", type: "button", onclick: onLock }, icon("lock"), "锁定"),
          h("button", { class: "button button-quiet", type: "button", onclick: onSignOut }, icon("out"), "退出")))),
    page));
  const refresh = () => {
    for (const route of routes) {
      const count = route.count && links.get(route.path).querySelector(".toc-count");
      if (count) count.textContent = route.count() ?? "";
    }
  };
  refresh();
  return { page, refresh, setCurrent: (path) => markCurrent(links, path) };
}

function phoneShell(root, { routes, onLock, onSignOut }) {
  const links = new Map();
  const rest = routes.filter((route) => !route.tab);
  const more = h("button", { class: "tabbar-item", type: "button", onclick: () => openMore() },
    icon("dots"), h("span", null, "更多"));
  const tabs = h("nav", { class: "tabbar", "aria-label": "目录" },
    routes.filter((route) => route.tab).map((route) => {
      const link = h("a", { class: "tabbar-item", href: `#/${route.path}` }, icon(route.icon), h("span", null, route.label));
      links.set(route.path, link);
      return link;
    }),
    more);
  const page = h("main", { class: "page", id: "page", tabindex: "-1" });
  clear(root).append(h("div", { class: "m-shell" },
    h("header", { class: "appbar" },
      h("a", { class: "brand", href: "#/overview" }, mascot(34), h("span", { class: "brand-name" }, "Egorov")),
      h("button", { class: "icon-button appbar-lock", type: "button", title: "锁定", "aria-label": "锁定", onclick: onLock },
        icon("lock"))),
    page,
    tabs));

  function openMore() {
    modal({
      title: "更多",
      sheet: true,
      build: (close) => h("div", { class: "modal-body m-more" },
        h("p", { class: "m-who" }, h("span", null, "管理员"), h("strong", null, session.username)),
        h("ul", { class: "m-menu" },
          rest.map((route) => h("li", null,
            h("a", { class: "m-menu-item", href: `#/${route.path}`, onclick: () => close() },
              icon(route.icon), h("span", null, route.label), icon("chevron")))),
          installMode() ? h("li", null, h("button", { class: "m-menu-item", type: "button", onclick: () => { close(); install(); } },
            icon("install"), h("span", null, "安装到主屏幕"))) : null,
          h("li", null, h("button", { class: "m-menu-item is-danger", type: "button", onclick: () => { close(); onSignOut(); } },
            icon("out"), h("span", null, "退出登录"))))),
    });
  }

  return {
    page,
    refresh() {},
    setCurrent(path) {
      markCurrent(links, path);
      if (rest.some((route) => route.path === path)) more.setAttribute("aria-current", "page");
      else more.removeAttribute("aria-current");
    },
  };
}

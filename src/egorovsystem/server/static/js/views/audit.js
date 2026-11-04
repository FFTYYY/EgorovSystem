// The ledger: who read or changed what, and when.

import { api } from "../api.js";
import { busy, clear, formatClock, formatDate, formatTime, h } from "../dom.js";
import { isMobile } from "../layout.js";

const ACTIONS = {
  "variable.read": "读取变量",
  "function.read": "读取函数",
  "variable.create": "新建变量",
  "function.create": "新建函数",
  "variable.update": "修改变量",
  "function.update": "修改函数",
  "variable.rename": "重命名变量",
  "function.rename": "重命名函数",
  "variable.delete": "删除变量",
  "function.delete": "删除函数",
  "device.create": "创建设备令牌",
  "device.update": "修改设备",
  "device.delete": "删除设备",
  "device.auth": "设备认证",
  "login": "登录后台",
  "logout": "退出登录",
  "admin.reauth": "确认主密码",
  "admin.password": "修改主密码",
  "admin.password.reset": "用恢复密钥重置主密码",
  "admin.recovery": "生成新的恢复密钥",
  "admin.totp.enable": "开启两步验证",
  "admin.totp.disable": "关闭两步验证",
  "session.revoke": "让会话下线",
  "session.revoke_others": "让其他会话下线",
  "session.revoke_all": "让所有会话下线",
  "vault.init": "初始化保险库",
  "vault.rotate": "轮换保险库密钥",
  "vault.import_legacy": "导入旧数据",
};

export function actorName(actor, devices) {
  if (actor === "admin") return "后台";
  if (actor === "cli") return "服务器命令行";
  if (actor === "anonymous") return "未登录访客";
  if (actor.startsWith("device:")) {
    const id = actor.slice(7);
    const device = devices.get(id);
    return device ? device.name : `已删除的设备 ${id}`;
  }
  return actor;
}

export function ledgerRow(entry, devices, compact = false) {
  const action = ACTIONS[entry.action] || entry.action;
  return h("tr", { class: entry.ok ? null : "is-failed" },
    h("td", { class: "ledger-time" }, h("time", { datetime: new Date(entry.ts * 1000).toISOString() }, formatTime(entry.ts))),
    h("td", null, actorName(entry.actor, devices)),
    h("td", null, action, entry.ok ? null : h("span", { class: "tag tag-danger" }, "失败")),
    h("td", null, entry.target ? h("code", null, entry.target) : null),
    compact ? null : h("td", { class: "ledger-ip" }, entry.ip || ""));
}

function ledgerHead(compact) {
  return h("thead", null, h("tr", null, h("th", null, "时间"), h("th", null, "谁"), h("th", null, "做了什么"),
    h("th", null, "对象"), compact ? null : h("th", null, "IP")));
}

function dayLabel(ts) {
  const day = new Date(ts * 1000);
  const today = new Date();
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (day.toDateString() === today.toDateString()) return "今天";
  if (day.toDateString() === yesterday.toDateString()) return "昨天";
  return formatDate(ts);
}

// One entry in the phone ledger: what and on what, then who and from where.
function ledgerItem(entry, devices) {
  return h("li", { class: ["m-entry", !entry.ok && "is-failed"] },
    h("div", { class: "m-entry-what" }, ACTIONS[entry.action] || entry.action,
      entry.ok ? null : h("span", { class: "tag tag-danger" }, "失败"),
      entry.target ? h("code", null, entry.target) : null),
    h("time", { class: "m-entry-time", datetime: new Date(entry.ts * 1000).toISOString(), title: formatTime(entry.ts) },
      formatClock(entry.ts)),
    h("div", { class: "m-entry-who" }, [actorName(entry.actor, devices), entry.ip].filter(Boolean).join(" · ")));
}

// A ledger that grows as older entries load: a table on desktop, a list
// grouped by day on phones.
function ledgerView(devices, compact) {
  if (isMobile()) {
    const list = h("ul", { class: "m-ledger" });
    let day = null;
    return {
      node: list,
      size: () => list.querySelectorAll(".m-entry").length,
      append(entries) {
        for (const entry of entries) {
          const label = dayLabel(entry.ts);
          if (label !== day) {
            day = label;
            list.append(h("li", { class: "m-day" }, label));
          }
          list.append(ledgerItem(entry, devices));
        }
      },
    };
  }
  const rows = h("tbody");
  return {
    node: h("div", { class: "table-wrap" }, h("table", { class: "ledger" }, ledgerHead(compact), rows)),
    size: () => rows.children.length,
    append(entries) { for (const entry of entries) rows.append(ledgerRow(entry, devices, compact)); },
  };
}

export function ledger(entries, devices, compact = false) {
  const view = ledgerView(devices, compact);
  view.append(entries);
  return view.node;
}

export async function deviceMap() {
  try {
    const { devices } = await api("GET", "/devices");
    return new Map(devices.map((d) => [d.id, d]));
  } catch {
    return new Map();
  }
}

const FILTERS = [
  { key: "all", label: "全部", query: {} },
  { key: "reads", label: "设备读取", query: { actor: "device:" } },
  { key: "admin", label: "后台操作", query: { actor: "admin" } },
  { key: "failed", label: "仅失败", query: { failed: "1" } },
];

export async function renderAudit(page, ctx) {
  const initial = ctx.params.get("failed") === "1" ? "failed" : "all";
  const devices = await deviceMap();
  const body = h("div");
  const filterBar = h("div", { class: "segmented filter-bar", role: "radiogroup", "aria-label": "筛选" });

  page.append(
    h("header", { class: "page-head" },
      h("div", null,
        h("h1", null, "记录"),
        h("p", { class: "page-lead" }, "谁在什么时候读了什么、改了什么。服务器能看到这些元数据，但看不到内容。"))),
    filterBar, body);

  let active = initial;
  async function show(key) {
    active = key;
    for (const button of filterBar.children) button.setAttribute("aria-checked", String(button.dataset.key === key));
    const filter = FILTERS.find((f) => f.key === key);
    clear(body);
    let before = null;
    const view = ledgerView(devices, false);
    const more = h("button", { class: "button", type: "button" }, "加载更早的记录");
    const empty = h("p", { class: "empty" }, "没有符合条件的记录。");
    empty.hidden = true;
    body.append(view.node, empty, h("div", { class: "more-row" }, more));

    async function load() {
      const query = new URLSearchParams({ limit: "60", ...filter.query });
      if (before) query.set("before", String(before));
      const data = await api("GET", `/audit?${query}`);
      if (active !== key || !ctx.isCurrent()) return;
      view.append(data.entries);
      before = data.next_before;
      more.hidden = !before;
      empty.hidden = view.size() > 0;
      view.node.hidden = view.size() === 0;
    }
    more.addEventListener("click", () => busy(more, "正在加载…", load));
    await load();
  }

  for (const filter of FILTERS) {
    filterBar.append(h("button", { type: "button", role: "radio", class: "segment", dataset: { key: filter.key },
      "aria-checked": String(filter.key === initial), onclick: () => show(filter.key) },
      filter.label));
  }
  await show(initial);
}

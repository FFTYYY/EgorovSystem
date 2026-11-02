// Overview: a few sentences about the vault instead of a wall of numbers.

import { api } from "../api.js";
import { h } from "../dom.js";
import { deviceMap, ledger } from "./audit.js";

function attention(text, label, onClick) {
  return h("li", { class: "attention-item" }, h("p", null, text),
    h("button", { class: "button", type: "button", onclick: onClick }, label));
}

export async function renderOverview(page, ctx) {
  const [data, devices] = await Promise.all([api("GET", "/overview"), deviceMap()]);
  if (!ctx.isCurrent()) return;
  ctx.setDeviceCount(data.counts.devices);
  const { variables, functions, devices_enabled: enabled } = data.counts;

  const summary = h("p", { class: "summary" },
    `保险库里有 ${variables} 个变量和 ${functions} 个函数，${enabled} 台设备可以读取它们。`,
    `过去 24 小时有 ${data.reads_24h} 次读取`,
    data.failures_24h ? `，另有 ${data.failures_24h} 次失败的访问。` : "，没有失败的访问。");

  const items = [];
  if (!data.account.totp_enabled) {
    items.push(attention("两步验证没有开启。开启后，即使主密码泄露，别人也无法登录后台。", "开启两步验证",
      () => ctx.go("settings")));
  }
  if (data.failures_24h) {
    items.push(attention(`过去 24 小时有 ${data.failures_24h} 次失败的访问。看看是哪里来的。`, "查看失败记录",
      () => ctx.go("audit?failed=1")));
  }
  if (!data.counts.devices) {
    items.push(attention("还没有设备令牌。为每台需要读取保险库的机器创建一个。", "创建设备令牌", () => ctx.go("devices")));
  }
  if (!variables && !functions) {
    items.push(attention("保险库还是空的。新建一个变量，或者在服务器上导入旧数据。", "新建变量", () => ctx.go("variables")));
  }

  const host = location.host;
  page.append(
    h("header", { class: "page-head" }, h("div", null, h("h1", null, "总览"))),
    summary,
    items.length ? h("section", { class: "block" }, h("h2", null, "需要处理"), h("ul", { class: "attention" }, items)) : null,
    h("section", { class: "block" },
      h("h2", null, "最近的记录"),
      data.recent.length ? ledger(data.recent, devices, true) : h("p", { class: "empty" }, "还没有记录。"),
      h("p", { class: "block-foot" }, h("a", { href: "#/audit" }, "查看全部记录"))),
    h("section", { class: "block" },
      h("h2", null, "在新机器上使用"),
      h("ol", { class: "steps" },
        h("li", null, "安装客户端：", h("code", null, "pip install -U egorovsystem")),
        h("li", null, "在", h("a", { href: "#/devices" }, "设备"), "页面为这台机器创建令牌。"),
        h("li", null, "设置 ", h("code", null, `EGOROV_SERVER=${host}`), " 和 ", h("code", null, "EGOROV_PWD=<令牌>"),
          "，或者运行 ", h("code", null, "egolocal-run"), "。之后就能用 ", h("code", null, "EGO 名字"), " 和 ",
          h("code", null, 'Egorov["名字"]'), " 了。"))));
}

// Devices: one token per machine, with scopes, expiry and revocation.

import * as c from "../crypto.js";
import { api } from "../api.js";
import { busy, clear, confirmAction, copyText, field, formError, formatDate, h, icon, modal, timeTag, toast } from "../dom.js";
import { itemsOf, session } from "../state.js";

const SCOPE_RE = /^(?:(variable|function):)?([A-Za-z0-9_.\-*?[\]]{1,128})$/;

function globToRegExp(pattern) {
  let out = "";
  for (let i = 0; i < pattern.length; i++) {
    const ch = pattern[i];
    if (ch === "*") out += ".*";
    else if (ch === "?") out += ".";
    else if (ch === "[") {
      const end = pattern.indexOf("]", i + 1);
      if (end < 0) out += "\\[";
      else {
        let body = pattern.slice(i + 1, end).replace(/\\/g, "\\\\");
        if (body.startsWith("!")) body = `^${body.slice(1)}`;
        out += `[${body}]`;
        i = end;
      }
    } else out += ch.replace(/[.+^${}()|\\-]/g, "\\$&");
  }
  return new RegExp(`^${out}$`);
}

function scopeAllows(scopes, kind, name) {
  return scopes.some((scope) => {
    const match = SCOPE_RE.exec(scope);
    if (!match || (match[1] && match[1] !== kind)) return false;
    return globToRegExp(match[2]).test(name);
  });
}

function parseScopes(text) {
  const scopes = text.split("\n").map((line) => line.trim()).filter(Boolean);
  const bad = scopes.find((scope) => !SCOPE_RE.test(scope));
  return { scopes, bad };
}

function coverage(scopes) {
  const vars = itemsOf("variable").filter((i) => scopeAllows(scopes, "variable", i.name)).length;
  const fns = itemsOf("function").filter((i) => scopeAllows(scopes, "function", i.name)).length;
  return `按现在的条目，这个令牌能读取 ${vars} 个变量和 ${fns} 个函数。`;
}

const EXPIRY = [
  { value: "", label: "永不过期" },
  { value: "30", label: "30 天后" },
  { value: "90", label: "90 天后" },
  { value: "365", label: "1 年后" },
];

function scopeEditor(initial) {
  const all = !initial || (initial.length === 1 && initial[0] === "*");
  const text = h("textarea", { class: "mono", rows: "4", spellcheck: "false", autocomplete: "off",
    placeholder: "openai_*\nvariable:aws_*\nfunction:deploy" });
  text.value = all ? "" : initial.join("\n");
  const group = h("div", { class: "segmented", role: "radiogroup", "aria-label": "可读范围" });
  const preview = h("p", { class: "field-hint" });
  const custom = h("div", { class: "scope-custom" },
    field("规则", text, "每行一条。* 匹配任意字符，? 匹配一个字符。前面加 variable: 或 function: 只匹配一种条目。"));
  let mode = all ? "all" : "custom";
  function update() {
    for (const b of group.children) b.setAttribute("aria-checked", String(b.dataset.value === mode));
    custom.hidden = mode === "all";
    const { scopes, bad } = mode === "all" ? { scopes: ["*"] } : parseScopes(text.value);
    preview.textContent = bad ? `这条规则的格式不对：${bad}` : scopes.length ? coverage(scopes) : "至少写一条规则。";
  }
  for (const [value, label] of [["all", "全部条目"], ["custom", "指定范围"]]) {
    group.append(h("button", { type: "button", role: "radio", class: "segment", dataset: { value },
      onclick: () => { mode = value; update(); if (value === "custom") text.focus(); } }, label));
  }
  text.addEventListener("input", update);
  update();
  return {
    node: h("div", { class: "field" }, h("span", { class: "field-label" }, "可读范围"), group, custom, preview),
    read() {
      if (mode === "all") return { scopes: ["*"] };
      const { scopes, bad } = parseScopes(text.value);
      if (bad) return { error: `这条规则的格式不对：${bad}` };
      if (!scopes.length) return { error: "至少写一条规则，或者选择“全部条目”。" };
      return { scopes };
    },
  };
}

function expirySelect(includeKeep) {
  const select = h("select", null,
    includeKeep ? h("option", { value: "keep" }, "保持不变") : null,
    EXPIRY.map((e) => h("option", { value: e.value }, e.label)));
  return {
    select,
    read() {
      if (select.value === "keep") return undefined;
      return select.value ? Math.floor(Date.now() / 1000) + Number(select.value) * 86400 : null;
    },
  };
}

function snippet(title, text) {
  return h("div", { class: "snippet" },
    h("div", { class: "snippet-head" }, h("span", null, title),
      h("button", { class: "button button-quiet", type: "button", onclick: () => copyText(text, title) }, icon("copy"), "复制")),
    h("pre", { class: "code-block" }, h("code", null, text)));
}

function showToken(device, token) {
  // Bare host name in production (the client assumes https); full origin for local http tests.
  const host = location.protocol === "https:" ? location.host : location.origin;
  return modal({
    title: `${device.name} 的令牌`,
    wide: true,
    dismissable: false,
    build: (close) => h("div", { class: "modal-body" },
      h("p", { class: "callout" }, "令牌只显示这一次。关闭后无法再次查看，只能删除这台设备后重新创建。"),
      h("div", { class: "token-box" },
        h("code", { class: "token" }, token),
        h("button", { class: "button button-primary", type: "button", onclick: () => copyText(token, "令牌") }, icon("copy"), "复制令牌")),
      h("h3", { class: "modal-subhead" }, "在这台机器上配置"),
      snippet("Windows（PowerShell）", `setx EGOROV_SERVER "${host}"\nsetx EGOROV_PWD "${token}"`),
      snippet("macOS / Linux", `echo 'export EGOROV_SERVER="${host}"' >> ~/.profile\necho 'export EGOROV_PWD="${token}"' >> ~/.profile`),
      snippet("不想把令牌放进环境变量时", `egolocal-run --server ${host}\n# 按提示粘贴令牌；同一台机器上的程序会通过它读取`),
      h("div", { class: "modal-actions" },
        h("button", { class: "button button-primary", type: "button", autofocus: true, onclick: () => close(true) }, "我已保存令牌"))),
  });
}

function createDevice(onCreated) {
  const name = h("input", { type: "text", maxlength: "80", required: true, placeholder: "例如：办公室台式机" });
  const scopes = scopeEditor(null);
  const expiry = expirySelect(false);
  const error = formError();
  return modal({
    title: "创建设备令牌",
    wide: true,
    build: (close) => {
      const submit = h("button", { class: "button button-primary", type: "submit" }, "创建令牌");
      const form = h("form", { class: "modal-body", novalidate: true },
        field("设备名称", name, "只用来在后台区分设备。"),
        scopes.node,
        field("有效期", expiry.select),
        error.node,
        h("div", { class: "modal-actions" },
          h("button", { class: "button", type: "button", onclick: () => close() }, "取消"), submit));
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        error.hide();
        if (!name.value.trim()) {
          error.show("给这台设备起个名字。");
          name.focus();
          return;
        }
        const scope = scopes.read();
        if (scope.error) {
          error.show(scope.error);
          return;
        }
        await busy(submit, "正在生成…", async () => {
          try {
            const material = await c.provisionDevice(session.vaultKey);
            const device = await api("POST", "/devices", {
              id: material.id, name: name.value.trim(), auth_hash: material.authHash,
              wrapped_vault_key: material.wrappedVaultKey, scopes: scope.scopes, expires_at: expiry.read(),
            });
            close(device);
            await showToken(device, material.token);
            onCreated(device);
          } catch (err) {
            error.show(err.message);
          }
        });
      });
      return form;
    },
  });
}

function editDevice(device, onSaved) {
  const name = h("input", { type: "text", maxlength: "80", required: true, value: device.name });
  const scopes = scopeEditor(device.scopes);
  const expiry = expirySelect(true);
  const error = formError();
  return modal({
    title: `编辑 ${device.name}`,
    wide: true,
    build: (close) => {
      const submit = h("button", { class: "button button-primary", type: "submit" }, "保存");
      const form = h("form", { class: "modal-body", novalidate: true },
        field("设备名称", name), scopes.node,
        field("有效期", expiry.select, device.expires_at ? `目前在 ${formatDate(device.expires_at)} 过期。` : "目前永不过期。"),
        error.node,
        h("div", { class: "modal-actions" },
          h("button", { class: "button", type: "button", onclick: () => close() }, "取消"), submit));
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        error.hide();
        const scope = scopes.read();
        if (scope.error) {
          error.show(scope.error);
          return;
        }
        const body = { name: name.value.trim(), scopes: scope.scopes };
        const expires = expiry.read();
        if (expires !== undefined) body.expires_at = expires;
        await busy(submit, "正在保存…", async () => {
          try {
            const saved = await api("PATCH", `/devices/${device.id}`, body);
            toast(`已保存 ${saved.name}`);
            close(saved);
            onSaved();
          } catch (err) {
            error.show(err.message);
          }
        });
      });
      return form;
    },
  });
}

function status(device) {
  const now = Date.now() / 1000;
  if (!device.enabled) return h("span", { class: "tag tag-muted" }, "已停用");
  if (device.expires_at && device.expires_at < now) return h("span", { class: "tag tag-danger" }, "已过期");
  if (device.expires_at) return h("span", { class: "tag tag-ok", title: `${formatDate(device.expires_at)} 过期` }, `启用 · ${formatDate(device.expires_at)} 过期`);
  return h("span", { class: "tag tag-ok" }, "启用");
}

export async function renderDevices(page, ctx) {
  const tbody = h("tbody");
  const empty = h("div", { class: "empty" },
    h("p", null, "还没有设备。为每台需要读取保险库的机器创建一个令牌，这样某台机器丢了也只需要吊销它自己。"));
  const table = h("div", { class: "table-wrap" },
    h("table", { class: "catalog" },
      h("thead", null, h("tr", null, h("th", null, "名称"), h("th", null, "可读范围"), h("th", null, "状态"),
        h("th", null, "最后使用"), h("th", null, h("span", { class: "visually-hidden" }, "操作")))),
      tbody));

  async function load() {
    const { devices } = await api("GET", "/devices");
    if (!ctx.isCurrent()) return;
    ctx.setDeviceCount(devices.length);
    clear(tbody);
    for (const device of devices) {
      const scopes = device.scopes.length === 1 && device.scopes[0] === "*"
        ? h("span", { class: "muted" }, "全部条目")
        : device.scopes.map((scope) => h("code", { class: "ref" }, scope));
      tbody.append(h("tr", { class: device.enabled ? null : "is-disabled" },
        h("td", { class: "cell-name" }, h("span", { class: "device-name" }, device.name), h("code", { class: "device-id" }, device.id)),
        h("td", { class: "cell-scopes" }, scopes),
        h("td", { class: "cell-status" }, status(device)),
        h("td", { class: "cell-time" }, timeTag(device.last_used_at),
          device.last_used_ip ? h("span", { class: "device-ip" }, device.last_used_ip) : null),
        h("td", { class: "cell-actions" },
          h("button", { class: "button button-quiet", type: "button", onclick: () => toggle(device) },
            device.enabled ? "停用" : "启用"),
          h("button", { class: "icon-button", type: "button", title: "编辑", "aria-label": `编辑 ${device.name}`,
            onclick: () => editDevice(device, load) }, icon("edit")),
          h("button", { class: "icon-button danger", type: "button", title: "删除", "aria-label": `删除 ${device.name}`,
            onclick: () => remove(device) }, icon("trash")))));
    }
    table.hidden = devices.length === 0;
    empty.hidden = devices.length !== 0;
  }

  async function toggle(device) {
    try {
      await api("PATCH", `/devices/${device.id}`, { enabled: !device.enabled });
      toast(device.enabled ? `已停用 ${device.name}` : `已启用 ${device.name}`);
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function remove(device) {
    const ok = await confirmAction({
      title: `删除设备 ${device.name}？`,
      message: "这台机器会立刻无法读取保险库。如果只是暂时不用，可以先停用。",
      confirmLabel: "删除设备",
      danger: true,
    });
    if (!ok) return;
    try {
      await api("DELETE", `/devices/${device.id}`);
      toast(`已删除 ${device.name}`);
      await load();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  page.append(
    h("header", { class: "page-head" },
      h("div", null, h("h1", null, "设备"),
        h("p", { class: "page-lead" }, "每台机器一个令牌。令牌只在创建时显示一次，服务器只保存它的哈希，无法用它解密。")),
      h("button", { class: "button button-primary", type: "button", onclick: () => createDevice(load) }, icon("plus"), "创建设备令牌")),
    table, empty);
  await load();
}

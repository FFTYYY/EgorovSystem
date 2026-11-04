// Which items a device token may read: glob rules like openai_* or
// function:deploy, an editor for them with checkboxes, and the same
// "functions bring their variables" rule the server applies.

import { field, h } from "../dom.js";
import { isMobile } from "../layout.js";
import { functionRefs, itemsOf } from "../state.js";

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

// Variables a device gets because a function it may read uses them
// (the server applies the same rule): variable name -> first such function.
function grantedByFunctions(scopes) {
  const granted = new Map();
  for (const fn of itemsOf("function")) {
    if (!scopeAllows(scopes, "function", fn.name)) continue;
    for (const ref of functionRefs(fn)) if (!granted.has(ref)) granted.set(ref, fn.name);
  }
  return granted;
}

export function extraVariables(scopes) {
  const granted = grantedByFunctions(scopes);
  return itemsOf("variable").filter((v) => !scopeAllows(scopes, "variable", v.name) && granted.has(v.name)).length;
}

function coverage(scopes) {
  const direct = itemsOf("variable").filter((i) => scopeAllows(scopes, "variable", i.name)).length;
  const extra = extraVariables(scopes);
  const fns = itemsOf("function").filter((i) => scopeAllows(scopes, "function", i.name)).length;
  const note = extra ? `，其中 ${extra} 个变量是随函数自动包含的` : "";
  return `按现在的条目，这个令牌能读取 ${fns} 个函数和 ${direct + extra} 个变量${note}。`;
}

// Checkboxes that write rules into the text box; the text box stays the
// source of truth, so typing rules by hand keeps working.
function scopePicker(text, onChange) {
  const boxes = [];
  const lines = () => text.value.split("\n").map((line) => line.trim()).filter(Boolean);
  const toggle = (rule, on) => {
    const kept = lines().filter((line) => line !== rule);
    if (on) kept.push(rule);
    text.value = kept.join("\n");
    onChange();
  };
  const column = (title, kind) => {
    const items = itemsOf(kind);
    const list = h("div", { class: "picker-list" });
    for (const item of items) {
      const rule = `${kind}:${item.name}`;
      const input = h("input", { type: "checkbox" });
      const note = h("span", { class: "picker-note" });
      const row = h("label", { class: "picker-item" }, input, h("code", null, item.name), note);
      input.addEventListener("change", () => toggle(rule, input.checked));
      boxes.push({ kind, name: item.name, rule, input, note, row });
      list.append(row);
    }
    if (!items.length) list.append(h("p", { class: "picker-empty" }, kind === "function" ? "还没有函数" : "还没有变量"));
    return h("fieldset", { class: "picker-col" }, h("legend", null, title), list);
  };
  const filter = h("input", { type: "search", class: "picker-filter", placeholder: "筛选", "aria-label": "筛选条目",
    spellcheck: "false", autocomplete: "off" });
  filter.addEventListener("input", () => {
    const q = filter.value.trim().toLowerCase();
    for (const box of boxes) box.row.hidden = Boolean(q) && !box.name.toLowerCase().includes(q);
  });
  return {
    node: h("div", { class: "scope-picker" },
      h("div", { class: "picker-head" }, h("span", { class: "field-label" }, "勾选条目"), filter),
      h("div", { class: "picker-cols" }, column("函数", "function"), column("变量", "variable")),
      h("p", { class: "field-hint" }, "勾选会在下面的规则里加一行，取消就删掉那一行。授权一个函数时，它引用的变量会自动包含。")),
    sync(scopes) {
      const valid = scopes.filter((scope) => SCOPE_RE.test(scope));
      const granted = grantedByFunctions(valid);
      for (const box of boxes) {
        const explicit = valid.includes(box.rule);
        const covered = scopeAllows(valid, box.kind, box.name);
        const via = box.kind === "variable" && !covered ? granted.get(box.name) : null;
        box.input.checked = covered || Boolean(via);
        box.input.disabled = (covered && !explicit) || Boolean(via);
        box.note.textContent = explicit ? "" : covered ? "规则已覆盖" : via ? `随 ${via}` : "";
        box.row.title = explicit ? "" : covered ? "被通配规则覆盖，改规则才能取消" : via ? `函数 ${via} 引用了它，所以自动包含` : "";
        box.row.classList.toggle("is-implied", !explicit && (covered || Boolean(via)));
      }
    },
  };
}

export function scopeEditor(initial) {
  const all = !initial || (initial.length === 1 && initial[0] === "*");
  const text = h("textarea", { class: "mono", rows: "4", spellcheck: "false", autocomplete: "off",
    placeholder: "openai_*\nvariable:aws_*\nfunction:deploy" });
  text.value = all ? "" : initial.join("\n");
  const group = h("div", { class: "segmented", role: "radiogroup", "aria-label": "可读范围" });
  const preview = h("p", { class: "field-hint" });
  const picker = scopePicker(text, () => update());
  const custom = h("div", { class: "scope-custom" }, picker.node,
    field("规则", text, "每行一条，也可以直接打字。* 匹配任意字符，? 匹配一个字符。前面加 variable: 或 function: 只匹配一种条目。"));
  let mode = all ? "all" : "custom";
  function update() {
    for (const b of group.children) b.setAttribute("aria-checked", String(b.dataset.value === mode));
    custom.hidden = mode === "all";
    const { scopes, bad } = mode === "all" ? { scopes: ["*"] } : parseScopes(text.value);
    if (mode !== "all") picker.sync(scopes);
    preview.textContent = bad ? `这条规则的格式不对：${bad}` : scopes.length ? coverage(scopes) : "至少勾选一项，或写一条规则。";
  }
  for (const [value, label] of [["all", "全部条目"], ["custom", "指定范围"]]) {
    group.append(h("button", { type: "button", role: "radio", class: "segment", dataset: { value },
      onclick: () => {
        mode = value;
        update();
        // On phones the checkboxes come first; focusing the rules would pop up the keyboard.
        if (value === "custom" && !isMobile()) text.focus();
      } }, label));
  }
  text.addEventListener("input", update);
  update();
  return {
    node: h("div", { class: "field" }, h("span", { class: "field-label" }, "可读范围"), group, custom, preview),
    read() {
      if (mode === "all") return { scopes: ["*"] };
      const { scopes, bad } = parseScopes(text.value);
      if (bad) return { error: `这条规则的格式不对：${bad}` };
      if (!scopes.length) return { error: "至少勾选一项或写一条规则，或者选择“全部条目”。" };
      return { scopes };
    },
  };
}

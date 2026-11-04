// Variables: the catalogue of secrets and settings.

import { ago, busy, clear, confirmAction, copyText, field, formatTime, formError, h, hoverTip, icon, modal, timeTag, toast } from "../dom.js";
import * as m from "../mobile.js";
import { dragHandle } from "../sortable.js";
import { deleteItem, functionsUsing, NAME_RE, saveItem } from "../state.js";
import { renderCatalog } from "./catalog.js";

function nameInput(value) {
  return h("input", { type: "text", class: "mono", value, required: true, spellcheck: "false", autocomplete: "off",
    autocapitalize: "off", maxlength: "128", pattern: NAME_RE.source });
}

export function editVariable(existing, onSaved) {
  const name = nameInput(existing ? existing.name : "");
  const value = h("textarea", { class: "mono", rows: "4", spellcheck: "false", autocomplete: "off" });
  value.value = existing && existing.payload ? existing.payload.value : "";
  const note = h("input", { type: "text", value: existing && existing.payload ? existing.payload.note : "",
    maxlength: "500" });
  const error = formError();
  const users = existing ? functionsUsing(existing.name) : [];
  const renameWarning = h("p", { class: "field-warning" });
  renameWarning.hidden = true;
  name.addEventListener("input", () => {
    const renamed = existing && name.value.trim() !== existing.name && users.length;
    renameWarning.hidden = !renamed;
    if (renamed) {
      renameWarning.textContent = `这些函数引用了旧名字 \${${existing.name}}，改名后需要手动修改它们：${users.map((f) => f.name).join("、")}`;
    }
  });

  return modal({
    title: existing ? `编辑变量` : "新建变量",
    wide: true,
    build: (close) => {
      const submit = h("button", { class: "button button-primary", type: "submit" }, existing ? "保存" : "创建变量");
      const form = h("form", { class: "modal-body", novalidate: true },
        field("名字", name, "程序里用这个名字读取，例如 Egorov[\"openai_keys\"]。"),
        renameWarning,
        field("值", value, "可以有多行。保存前会在这个浏览器里加密。"),
        field("备注", note, "只有你在后台能看到，同样会加密。"),
        users.length ? h("p", { class: "field-hint" }, "被这些函数引用：", users.map((f) => h("code", { class: "ref" }, f.name))) : null,
        error.node,
        h("div", { class: "modal-actions" },
          h("button", { class: "button", type: "button", onclick: () => close() }, "取消"), submit));
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        error.hide();
        const newName = name.value.trim();
        if (!NAME_RE.test(newName)) {
          error.show("名字只能用字母、数字、下划线、点和连字符，最多 128 个字符，不能以点或连字符开头。");
          name.focus();
          return;
        }
        await busy(submit, "正在加密…", async () => {
          try {
            const saved = await saveItem("variable", existing ? existing.name : null, newName,
              { value: value.value, note: note.value.trim() });
            toast(existing ? `已保存 ${newName}` : `已创建 ${newName}`);
            close(saved);
            onSaved(saved);
          } catch (err) {
            error.show(err.message);
          }
        });
      });
      return form;
    },
  });
}

function valueCell(item) {
  if (!item.payload) return h("td", { class: "cell-error" }, "无法解密");
  const secret = h("code", { class: "secret" }, item.payload.value);
  const mask = h("span", { class: "mask", "aria-label": "已隐藏" }, "••••••••");
  secret.hidden = true;
  const toggle = h("button", { class: "icon-button", type: "button", "aria-label": "显示值", title: "显示值" }, icon("eye"));
  toggle.addEventListener("click", () => {
    const show = secret.hidden;
    secret.hidden = !show;
    mask.hidden = show;
    clear(toggle).append(icon(show ? "eyeOff" : "eye"));
    toggle.setAttribute("aria-label", show ? "隐藏值" : "显示值");
    toggle.title = show ? "隐藏值" : "显示值";
  });
  const copy = h("button", { class: "icon-button", type: "button", "aria-label": "复制值", title: "复制值",
    onclick: () => copyText(item.payload.value, `变量 ${item.name} 的值`) }, icon("copy"));
  return h("td", { class: "cell-value" }, h("div", { class: "value-line" }, mask, secret, h("span", { class: "row-tools" }, toggle, copy)));
}

// "Used by N functions" under a variable's name; the list shows on hover or focus.
function usedBy(name) {
  const users = functionsUsing(name);
  if (!users.length) return null;
  const label = `被 ${users.length} 个函数引用`;
  const trigger = h("button", { class: "used-by", type: "button", "aria-label": `${label}：${users.map((f) => f.name).join("、")}` },
    label);
  hoverTip(trigger, () => [h("span", { class: "tooltip-title" }, "引用它的函数"),
    ...users.map((f) => h("code", { class: "ref" }, f.name))]);
  return trigger;
}

async function removeVariable(item, changed) {
  const users = functionsUsing(item.name);
  const ok = await confirmAction({
    title: `删除变量 ${item.name}？`,
    message: [
      "读取它的程序会报“找不到”。这一步无法撤销。",
      users.length ? `这些函数引用了它：${users.map((f) => f.name).join("、")}。` : null,
    ].filter(Boolean),
    confirmLabel: "删除变量",
    danger: true,
  });
  if (!ok) return;
  try {
    await deleteItem("variable", item.name);
    toast(`已删除 ${item.name}`);
    changed();
  } catch (err) {
    toast(err.message, "error");
  }
}

// ------------------------------------------------------------ desktop table

function table() {
  const body = h("tbody");
  const node = h("div", { class: "table-wrap" },
    h("table", { class: "catalog" },
      h("thead", null, h("tr", null, h("th", { class: "cell-handle" }, h("span", { class: "visually-hidden" }, "顺序")),
        h("th", null, "名字"), h("th", null, "值"), h("th", null, "备注"),
        h("th", null, "修改于"), h("th", null, h("span", { class: "visually-hidden" }, "操作")))),
      body));
  return { node, body };
}

function tableRow(item, { sortable, changed }) {
  return h("tr", { dataset: { key: item.name } },
    h("td", { class: "cell-handle" }, dragHandle(item.name, sortable)),
    h("td", { class: "cell-name" }, h("code", null, item.name), usedBy(item.name)),
    valueCell(item),
    h("td", { class: "cell-note" }, item.payload ? item.payload.note : item.error),
    h("td", { class: "cell-time" }, timeTag(item.updated_at)),
    h("td", { class: "cell-actions" },
      h("button", { class: "icon-button", type: "button", title: "编辑", "aria-label": `编辑 ${item.name}`,
        disabled: !item.payload, onclick: () => editVariable(item, changed) }, icon("edit")),
      h("button", { class: "icon-button danger", type: "button", title: "删除", "aria-label": `删除 ${item.name}`,
        onclick: () => removeVariable(item, changed) }, icon("trash"))));
}

// ------------------------------------------------------------ phone list

// The value, hidden until asked for, with show and copy buttons below it.
function secretBox(value, name) {
  const text = h("code", { class: "m-secret-text" }, value);
  const mask = h("span", { class: "mask", "aria-label": "已隐藏" }, "••••••••••••");
  text.hidden = true;
  const toggle = h("button", { class: "button button-quiet", type: "button" });
  const show = (visible) => {
    text.hidden = !visible;
    mask.hidden = visible;
    clear(toggle).append(icon(visible ? "eyeOff" : "eye"), visible ? "隐藏" : "显示");
  };
  toggle.addEventListener("click", () => show(text.hidden));
  show(false);
  return [h("div", { class: "m-secret" }, mask, text),
    h("div", { class: "m-secret-tools" }, toggle,
      h("button", { class: "button button-quiet", type: "button", onclick: () => copyText(value, `变量 ${name} 的值`) },
        icon("copy"), "复制"))];
}

function showVariable(item, changed) {
  const p = item.payload;
  const users = functionsUsing(item.name);
  return m.detailSheet({
    title: item.name,
    heading: h("code", { class: "m-sheet-name" }, item.name),
    build: (close) => [
      m.detail("值", p ? secretBox(p.value, item.name) : h("span", { class: "cell-error" }, `无法解密：${item.error}`)),
      p && p.note ? m.detail("备注", p.note) : null,
      users.length ? m.detail("被这些函数引用", users.map((f) => h("code", { class: "ref" }, f.name))) : null,
      m.detail("修改于", `${formatTime(item.updated_at)}（${ago(item.updated_at)}）`),
      m.actions(
        m.sheetButton(close, { label: "编辑", iconName: "edit", disabled: !p, action: () => editVariable(item, changed) }),
        m.sheetButton(close, { label: "删除", iconName: "trash", kind: "danger", action: () => removeVariable(item, changed) })),
    ],
  });
}

function listRow(item, { changed }) {
  const p = item.payload;
  const users = functionsUsing(item.name).length;
  return m.row({
    key: item.name,
    label: `查看 ${item.name}`,
    head: h("code", { class: "m-row-title" }, item.name),
    lines: [
      p ? m.sub(p.note) : m.sub("无法解密", "cell-error"),
      m.meta(`修改于 ${ago(item.updated_at)}`, users ? `被 ${users} 个函数引用` : null),
    ],
    quick: p ? h("button", { class: "icon-button", type: "button", title: "复制值", "aria-label": `复制 ${item.name} 的值`,
      onclick: () => copyText(p.value, `变量 ${item.name} 的值`) }, icon("copy")) : null,
    onOpen: () => showVariable(item, changed),
  });
}

export async function renderVariables(page, ctx) {
  renderCatalog(page, ctx, {
    kind: "variable",
    title: "变量",
    lead: "键值对，比如 API 密钥。值在这个浏览器里加密后才会上传，服务器只保存密文。",
    newLabel: "新建变量",
    placeholder: "按名字或备注筛选",
    noMatch: "没有匹配的变量。",
    matches: (item, q) => item.name.toLowerCase().includes(q) || Boolean(item.payload && item.payload.note.toLowerCase().includes(q)),
    create: (changed) => editVariable(null, changed),
    emptyState: () => h("p", null, "还没有变量。新建一个，或者在服务器上运行 ", h("code", null, "egoserver-admin import-legacy"), " 导入旧数据。"),
    desktop: { container: table, selector: "tr", row: tableRow },
    phone: { row: listRow },
  });
}

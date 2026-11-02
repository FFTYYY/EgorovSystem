// Variables: the catalogue of secrets and settings.

import { busy, clear, confirmAction, copyText, field, formError, h, hoverTip, icon, modal, timeTag, toast } from "../dom.js";
import { deleteItem, functionsUsing, itemsOf, NAME_RE, reorder, saveItem } from "../state.js";
import { dragHandle, focusHandle, makeSortable } from "../sortable.js";

let slashHandler = null;

export function bindSearchShortcut(input) {
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

export async function renderVariables(page, ctx) {
  let filter = "";
  const tbody = h("tbody");
  const count = h("span", { class: "toolbar-count" });
  const empty = h("div", { class: "empty" });
  const table = h("div", { class: "table-wrap" },
    h("table", { class: "catalog" },
      h("thead", null, h("tr", null, h("th", { class: "cell-handle" }, h("span", { class: "visually-hidden" }, "顺序")),
        h("th", null, "名字"), h("th", null, "值"), h("th", null, "备注"),
        h("th", null, "修改于"), h("th", null, h("span", { class: "visually-hidden" }, "操作")))),
      tbody));

  makeSortable(tbody, "tr", (names, moved) => {
    reorder("variable", names).catch((err) => { toast(err.message, "error"); draw(); });
    draw();
    focusHandle(tbody, moved);
  });

  function draw() {
    const all = itemsOf("variable");
    const rows = all.filter((item) => !filter || item.name.toLowerCase().includes(filter)
      || (item.payload && item.payload.note.toLowerCase().includes(filter)));
    clear(tbody);
    for (const item of rows) {
      tbody.append(h("tr", { dataset: { key: item.name } },
        h("td", { class: "cell-handle" }, dragHandle(item.name, !filter)),
        h("td", { class: "cell-name" }, h("code", null, item.name), usedBy(item.name)),
        valueCell(item),
        h("td", { class: "cell-note" }, item.payload ? item.payload.note : item.error),
        h("td", { class: "cell-time" }, timeTag(item.updated_at)),
        h("td", { class: "cell-actions" },
          h("button", { class: "icon-button", type: "button", title: "编辑", "aria-label": `编辑 ${item.name}`,
            disabled: !item.payload, onclick: () => editVariable(item, () => { draw(); ctx.refreshNav(); }) }, icon("edit")),
          h("button", { class: "icon-button danger", type: "button", title: "删除", "aria-label": `删除 ${item.name}`,
            onclick: () => remove(item) }, icon("trash")))));
    }
    count.textContent = filter ? `${rows.length} / ${all.length} 个` : `${all.length} 个`;
    table.hidden = rows.length === 0;
    empty.hidden = rows.length !== 0;
    clear(empty).append(all.length
      ? h("p", null, "没有匹配的变量。")
      : h("p", null, "还没有变量。新建一个，或者在服务器上运行 ", h("code", null, "egoserver-admin import-legacy"), " 导入旧数据。"));
  }

  async function remove(item) {
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
      draw();
      ctx.refreshNav();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  const search = searchBox("按名字或备注筛选", (value) => { filter = value; draw(); });
  page.append(
    h("header", { class: "page-head" },
      h("div", null, h("h1", null, "变量"),
        h("p", { class: "page-lead" }, "键值对，比如 API 密钥。值在这个浏览器里加密后才会上传，服务器只保存密文。")),
      h("button", { class: "button button-primary", type: "button",
        onclick: () => editVariable(null, () => { draw(); ctx.refreshNav(); }) }, icon("plus"), "新建变量")),
    h("div", { class: "toolbar" }, search.node, count),
    table, empty);
  draw();
}

// Functions: commands and Python snippets that `EGO <name>` runs.

import { busy, clear, confirmAction, copyText, field, formError, h, icon, modal, timeTag, toast } from "../dom.js";
import { deleteItem, findItem, itemsOf, NAME_RE, references, reorder, saveItem } from "../state.js";
import { dragHandle, focusHandle, makeSortable } from "../sortable.js";
import { searchBox } from "./variables.js";

const TYPES = [
  { value: "cmd", label: "命令", hint: "交给系统 shell 执行：Windows 上是 cmd.exe，其他系统是 sh。" },
  { value: "python", label: "Python", hint: "用运行 EGO 的那个 Python 执行。带 PEP 723 依赖声明且装了 uv 时用 uv run。" },
];

function refChips(content) {
  return references(content).map((name) => {
    const known = findItem("variable", name);
    return h("code", { class: ["ref", !known && "ref-unknown"],
      title: known ? "会替换成这个变量的值" : "没有这个变量，运行时原样保留" }, name);
  });
}

// Tab indents inside the editor. Press Escape first to move focus with Tab.
function codeEditor(value) {
  const area = h("textarea", { class: "mono code-editor", rows: "14", spellcheck: "false", autocomplete: "off",
    autocapitalize: "off", wrap: "off" });
  area.value = value;
  let escaped = false;
  area.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      escaped = true;
      event.stopPropagation();
      event.preventDefault();
      return;
    }
    if (event.key === "Tab" && !escaped && !event.shiftKey) {
      event.preventDefault();
      area.setRangeText("    ", area.selectionStart, area.selectionEnd, "end");
      area.dispatchEvent(new Event("input"));
    }
    escaped = false;
  });
  return area;
}

export function editFunction(existing, onSaved) {
  const payload = existing && existing.payload ? existing.payload : { type: "cmd", content: "", note: "" };
  const name = h("input", { type: "text", class: "mono", value: existing ? existing.name : "", required: true,
    spellcheck: "false", autocomplete: "off", autocapitalize: "off", maxlength: "128" });
  const note = h("input", { type: "text", value: payload.note || "", maxlength: "500" });
  const content = codeEditor(payload.content || "");
  const typeHint = h("p", { class: "field-hint" });
  let type = payload.type === "python" ? "python" : "cmd";
  const typeGroup = h("div", { class: "segmented", role: "radiogroup", "aria-label": "类型" });
  function setType(value) {
    type = value;
    for (const button of typeGroup.children) button.setAttribute("aria-checked", String(button.dataset.value === value));
    typeHint.textContent = TYPES.find((t) => t.value === value).hint;
  }
  for (const t of TYPES) {
    typeGroup.append(h("button", { type: "button", role: "radio", class: "segment", dataset: { value: t.value },
      onclick: () => setType(t.value) }, t.label));
  }
  setType(type);
  const refs = h("p", { class: "field-hint refs-line" });
  const drawRefs = () => {
    const chips = refChips(content.value);
    clear(refs).append(...(chips.length ? ["引用的变量：", ...chips] : ["用 ${变量名} 引用变量，运行前会在客户端替换。"]));
  };
  content.addEventListener("input", drawRefs);
  drawRefs();
  const error = formError();

  return modal({
    title: existing ? "编辑函数" : "新建函数",
    wide: true,
    build: (close) => {
      const submit = h("button", { class: "button button-primary", type: "submit" }, existing ? "保存" : "创建函数");
      const form = h("form", { class: "modal-body", novalidate: true },
        h("div", { class: "field-row" },
          field("名字", name, "在机器上用 EGO 名字 运行。"),
          h("div", { class: "field" }, h("span", { class: "field-label" }, "类型"), typeGroup, typeHint)),
        field("内容", content, "Tab 键缩进；先按 Esc 再按 Tab 可以离开编辑框。"),
        refs,
        field("备注", note),
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
            const saved = await saveItem("function", existing ? existing.name : null, newName,
              { type, content: content.value, note: note.value.trim() });
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

// The first meaningful line: skip blank lines and, for Python, comments
// such as a PEP 723 header.
function firstLine(text, type) {
  const lines = (text || "").split("\n").filter((l) => l.trim());
  const line = (type === "python" && lines.find((l) => !l.trim().startsWith("#"))) || lines[0] || "";
  return line.length > 90 ? `${line.slice(0, 90)}…` : line;
}

export async function renderFunctions(page, ctx) {
  let filter = "";
  const list = h("ul", { class: "fn-list" });
  makeSortable(list, "li.fn", (names, moved) => {
    reorder("function", names).catch((err) => { toast(err.message, "error"); draw(); });
    draw();
    focusHandle(list, moved);
  });
  const count = h("span", { class: "toolbar-count" });
  const empty = h("div", { class: "empty" });

  function draw() {
    const all = itemsOf("function");
    const rows = all.filter((item) => !filter || item.name.toLowerCase().includes(filter)
      || (item.payload && `${item.payload.note}\n${item.payload.content}`.toLowerCase().includes(filter)));
    clear(list);
    for (const item of rows) {
      const p = item.payload;
      list.append(h("li", { class: "fn", dataset: { key: item.name } },
        dragHandle(item.name, !filter),
        h("div", { class: "fn-main" },
          h("div", { class: "fn-title" },
            h("code", { class: "fn-name" }, item.name),
            p ? h("span", { class: ["tag", p.type === "python" ? "tag-python" : "tag-cmd"] }, p.type === "python" ? "Python" : "命令")
              : h("span", { class: "tag tag-danger" }, "无法解密")),
          p && p.note ? h("p", { class: "fn-note" }, p.note) : null,
          p ? h("pre", { class: "fn-preview" }, h("code", null, firstLine(p.content, p.type))) : null,
          p && references(p.content).length ? h("p", { class: "fn-refs" }, "引用：", refChips(p.content)) : null),
        h("div", { class: "fn-side" },
          h("span", { class: "fn-time" }, "修改于 ", timeTag(item.updated_at)),
          h("div", { class: "fn-actions" },
            h("button", { class: "icon-button", type: "button", title: `复制运行命令：EGO ${item.name}`,
              "aria-label": `复制运行命令 EGO ${item.name}`,
              onclick: () => copyText(`EGO ${item.name}`, "运行命令") }, icon("terminal")),
            h("button", { class: "icon-button", type: "button", title: "复制函数内容（未替换变量）",
              "aria-label": `复制 ${item.name} 的内容`, disabled: !p,
              onclick: () => copyText(p.content, `${item.name} 的内容`) }, icon("copy")),
            h("button", { class: "icon-button", type: "button", title: "编辑", "aria-label": `编辑 ${item.name}`,
              disabled: !p, onclick: () => editFunction(item, () => { draw(); ctx.refreshNav(); }) }, icon("edit")),
            h("button", { class: "icon-button danger", type: "button", title: "删除", "aria-label": `删除 ${item.name}`,
              onclick: () => remove(item) }, icon("trash"))))));
    }
    count.textContent = filter ? `${rows.length} / ${all.length} 个` : `${all.length} 个`;
    list.hidden = rows.length === 0;
    empty.hidden = rows.length !== 0;
    clear(empty).append(h("p", null, all.length ? "没有匹配的函数。" : "还没有函数。新建一个命令或 Python 片段，然后在任何机器上用 EGO 名字 运行它。"));
  }

  async function remove(item) {
    const ok = await confirmAction({
      title: `删除函数 ${item.name}？`,
      message: `之后运行 EGO ${item.name} 会报“找不到”。这一步无法撤销。`,
      confirmLabel: "删除函数",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteItem("function", item.name);
      toast(`已删除 ${item.name}`);
      draw();
      ctx.refreshNav();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  const search = searchBox("按名字、备注或内容筛选", (value) => { filter = value; draw(); });
  page.append(
    h("header", { class: "page-head" },
      h("div", null, h("h1", null, "函数"),
        h("p", { class: "page-lead" }, "可以用 EGO 名字 运行的命令或 Python 代码。内容同样在浏览器里加密。")),
      h("button", { class: "button button-primary", type: "button",
        onclick: () => editFunction(null, () => { draw(); ctx.refreshNav(); }) }, icon("plus"), "新建函数")),
    h("div", { class: "toolbar" }, search.node, count),
    list, empty);
  draw();
}

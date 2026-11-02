// The bookplate: sign in, unlock after a reload, or explain an empty server.

import * as c from "../crypto.js";
import { api, ApiError } from "../api.js";
import { busy, clear, field, formError, h, seal } from "../dom.js";
import { applySession, session, unlock } from "../state.js";

function plate(...content) {
  return h("div", { class: "plate-stage" },
    h("section", { class: "plate" },
      h("div", { class: "plate-inner" },
        seal(88),
        h("h1", { class: "plate-title" }, "Egorov"),
        h("p", { class: "plate-motto", lang: "la" }, "Clavis in manu tua"),
        ...content)));
}

const KEY_NOTE = "主密码只在这个浏览器里使用。服务器只收到由它派生出的验证值，看不到密码，也看不到保险库里的内容。";

export function renderLogin(root, { message, onDone }) {
  const username = h("input", { type: "text", name: "username", autocomplete: "username", required: true,
    spellcheck: "false", autocapitalize: "off", autofocus: true });
  const password = h("input", { type: "password", name: "password", autocomplete: "current-password", required: true });
  const totp = h("input", { type: "text", name: "totp", inputmode: "numeric", autocomplete: "one-time-code",
    pattern: "[0-9 ]{6,7}", maxlength: "7" });
  const totpField = field("验证码", totp, "打开身份验证器，输入当前显示的 6 位数字。");
  totpField.hidden = true;
  const error = formError();
  if (message) error.show(message);
  const submit = h("button", { class: "button button-primary button-wide", type: "submit" }, "登录");
  let derived = null; // keep derived keys while the user types a TOTP code

  const form = h("form", { class: "plate-form", novalidate: true },
    field("用户名", username), field("主密码", password), totpField, error.node, submit,
    h("p", { class: "plate-note" }, KEY_NOTE));

  password.addEventListener("input", () => { derived = null; });
  username.addEventListener("input", () => { derived = null; });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hide();
    if (!username.value.trim() || !password.value) {
      error.show("请输入用户名和主密码。");
      return;
    }
    await busy(submit, "正在派生密钥…", async () => {
      try {
        if (!derived) {
          const { kdf } = await api("POST", "/prelogin", { username: username.value.trim() });
          derived = await c.deriveAdminKeys(password.value, kdf);
        }
        const body = { username: username.value.trim(), auth_key: c.b64e(derived.auth) };
        if (!totpField.hidden) body.totp_code = totp.value.replace(/\s/g, "");
        const data = await api("POST", "/login", body);
        applySession(data);
        session.vaultKey = await c.unwrapVaultKey(derived.wrap, data.wrapped_vault_key);
        derived.wrap.fill(0);
        derived = null;
        password.value = "";
        await onDone();
      } catch (err) {
        if (err instanceof ApiError && err.code === "totp_required") {
          totpField.hidden = false;
          totp.focus();
          return;
        }
        if (err instanceof ApiError && err.code === "invalid_credentials") derived = null;
        error.show(err.message);
        if (err instanceof ApiError && err.code === "invalid_totp") totp.select();
      }
    });
  });

  clear(root).append(plate(form));
  document.title = "登录 · Egorov";
}

export function renderUnlock(root, { onDone, onSignOut, message }) {
  const password = h("input", { type: "password", name: "password", autocomplete: "current-password",
    required: true, autofocus: true });
  const error = formError();
  if (message) error.show(message);
  const submit = h("button", { class: "button button-primary button-wide", type: "submit" }, "解锁");
  const form = h("form", { class: "plate-form", novalidate: true },
    h("p", { class: "plate-who" }, "已登录为 ", h("strong", null, session.username), "。保险库已锁定，输入主密码解锁。"),
    field("主密码", password), error.node, submit,
    h("button", { class: "link-button", type: "button", onclick: onSignOut }, "退出登录"));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.hide();
    await busy(submit, "正在解锁…", async () => {
      try {
        const auth = await unlock(password.value);
        auth.fill(0);
        password.value = "";
        await onDone();
      } catch (err) {
        error.show(err.message);
        password.select();
      }
    });
  });
  clear(root).append(plate(form));
  document.title = "解锁 · Egorov";
}

export function renderUninitialized(root) {
  clear(root).append(plate(
    h("div", { class: "plate-form" },
      h("p", null, "这台服务器还没有初始化。在服务器上运行下面的命令，创建管理员账号和保险库："),
      h("pre", { class: "code-block" }, h("code", null, "egoserver-init")),
      h("p", { class: "plate-note" }, "初始化必须在服务器上完成，这样别人无法抢先在网页上占用一台新服务器。"))));
  document.title = "未初始化 · Egorov";
}

export function renderFatal(root, message) {
  clear(root).append(plate(h("div", { class: "plate-form" }, h("p", { class: "form-error" }, message),
    h("button", { class: "button", type: "button", onclick: () => location.reload() }, "重新加载"))));
}

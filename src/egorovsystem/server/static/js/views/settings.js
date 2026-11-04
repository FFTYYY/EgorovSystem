// Settings: master password, two-factor sign-in, recovery key, sessions,
// vault key rotation.

import * as c from "../crypto.js";
import { api } from "../api.js";
import { ago, busy, clear, copyText, field, formatDate, formError, h, icon, modal, timeTag, toast } from "../dom.js";
import { isMobile } from "../layout.js";
import * as m from "../mobile.js";
import { install, installMode } from "../pwa.js";
import { loadItems, proveCurrentPassword, session, vault } from "../state.js";

function passwordProblem(password) {
  if (password.length < 12) return "至少 12 个字符。一句容易记住的长句子比复杂的短密码更好。";
  if (new Set(password).size < 5) return "用更多不同的字符。";
  if (session.username && password.toLowerCase().includes(session.username.toLowerCase())) return "不要包含用户名。";
  return null;
}

function passwordInput(autocomplete, autofocus = false) {
  return h("input", { type: "password", autocomplete, required: true, autofocus });
}

// Show a newly generated recovery key; the dialog only closes once the admin
// confirms they stored it.
function showRecoveryKey(text) {
  return modal({
    title: "新的恢复密钥",
    wide: true,
    dismissable: false,
    build: (close) => {
      const check = h("input", { type: "checkbox", id: "recovery-saved" });
      const done = h("button", { class: "button button-primary", type: "button", disabled: true, onclick: () => close(true) }, "完成");
      check.addEventListener("change", () => { done.disabled = !check.checked; });
      return h("div", { class: "modal-body" },
        h("p", null, "忘记主密码时，在服务器上运行 ", h("code", null, "egoserver-admin reset-password"), " 并输入这串密钥。它只显示这一次。"),
        h("div", { class: "token-box" }, h("code", { class: "token recovery" }, text),
          h("button", { class: "button", type: "button", onclick: () => copyText(text, "恢复密钥") }, icon("copy"), "复制")),
        h("p", { class: "callout" }, "把它写在纸上或存进密码管理器，不要和主密码放在一起。主密码和恢复密钥都丢失时，保险库无法恢复。"),
        h("label", { class: "check" }, check, "我已把恢复密钥保存在安全的地方"),
        h("div", { class: "modal-actions" }, done));
    },
  });
}

function changePassword(onDone) {
  const current = passwordInput("current-password", true);
  const next = passwordInput("new-password");
  const again = passwordInput("new-password");
  const error = formError();
  return modal({
    title: "修改主密码",
    build: (close) => {
      const submit = h("button", { class: "button button-primary", type: "submit" }, "修改主密码");
      const form = h("form", { class: "modal-body", novalidate: true },
        field("当前主密码", current), field("新主密码", next, "至少 12 个字符。"), field("再输入一次", again),
        h("p", { class: "field-hint" }, "修改后，其他浏览器上的登录会全部下线。设备令牌和恢复密钥不受影响。"),
        error.node,
        h("div", { class: "modal-actions" },
          h("button", { class: "button", type: "button", onclick: () => close() }, "取消"), submit));
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        error.hide();
        const problem = passwordProblem(next.value);
        if (problem) return error.show(problem);
        if (next.value !== again.value) return error.show("两次输入的新主密码不一样。");
        await busy(submit, "正在派生密钥…", async () => {
          try {
            const currentAuth = await proveCurrentPassword(current.value);
            const kdf = c.newKdfParams();
            const keys = await c.deriveAdminKeys(next.value, kdf);
            const wrapped = await c.wrapVaultKey(keys.wrap, session.vaultKey);
            await api("POST", "/password", { current_auth_key: currentAuth, kdf, auth_key: c.b64e(keys.auth), wrapped_vault_key: wrapped });
            keys.wrap.fill(0);
            session.kdf = kdf;
            session.wrapped = wrapped;
            toast("主密码已修改，其他会话已下线。");
            close(true);
            onDone();
          } catch (err) {
            error.show(err.message);
          }
        });
      });
      return form;
    },
  });
}

function enableTotp(onDone) {
  const current = passwordInput("current-password", true);
  const error = formError();
  return modal({
    title: "开启两步验证",
    build: (close) => {
      const body = h("div", { class: "modal-body" });
      const submit = h("button", { class: "button button-primary", type: "submit" }, "继续");
      const step1 = h("form", { novalidate: true },
        h("p", null, "开启后，登录后台时除了主密码，还需要手机上身份验证器（例如 Google Authenticator、1Password、Aegis）里的 6 位验证码。"),
        field("当前主密码", current), error.node,
        h("div", { class: "modal-actions" }, h("button", { class: "button", type: "button", onclick: () => close() }, "取消"), submit));
      step1.addEventListener("submit", async (event) => {
        event.preventDefault();
        error.hide();
        await busy(submit, "正在确认…", async () => {
          try {
            const setup = await api("POST", "/totp/setup", { current_auth_key: await proveCurrentPassword(current.value) });
            clear(body).append(step2(setup));
          } catch (err) {
            error.show(err.message);
          }
        });
      });

      function step2(setup) {
        const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "7", autofocus: true });
        const err2 = formError();
        const confirm = h("button", { class: "button button-primary", type: "submit" }, "开启两步验证");
        const form = h("form", { novalidate: true },
          h("p", null, "用身份验证器扫描二维码，或者手动输入密钥。"),
          h("div", { class: "totp-setup" },
            setup.qr ? h("img", { src: setup.qr, alt: "两步验证二维码", class: "qr", width: "180", height: "180" }) : null,
            h("div", null, h("p", { class: "field-label" }, "密钥"),
              h("code", { class: "token totp-secret" }, setup.secret.match(/.{1,4}/g).join(" ")))),
          field("身份验证器上的验证码", code), err2.node,
          h("div", { class: "modal-actions" }, h("button", { class: "button", type: "button", onclick: () => close() }, "取消"), confirm));
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          err2.hide();
          await busy(confirm, "正在验证…", async () => {
            try {
              await api("POST", "/totp/enable", { code: code.value.replace(/\s/g, "") });
              session.totpEnabled = true;
              toast("两步验证已开启。");
              close(true);
              onDone();
            } catch (err) {
              err2.show(err.message);
            }
          });
        });
        setTimeout(() => code.focus(), 0);
        return form;
      }

      body.append(step1);
      return body;
    },
  });
}

function disableTotp(onDone) {
  const current = passwordInput("current-password", true);
  const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: "7" });
  const error = formError();
  return modal({
    title: "关闭两步验证",
    build: (close) => {
      const submit = h("button", { class: "button button-danger", type: "submit" }, "关闭两步验证");
      const form = h("form", { class: "modal-body", novalidate: true },
        h("p", null, "关闭后，只凭主密码就能登录后台。"),
        field("当前主密码", current), field("身份验证器上的验证码", code), error.node,
        h("div", { class: "modal-actions" }, h("button", { class: "button", type: "button", onclick: () => close() }, "取消"), submit));
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        error.hide();
        await busy(submit, "正在确认…", async () => {
          try {
            await api("POST", "/totp/disable", {
              current_auth_key: await proveCurrentPassword(current.value), code: code.value.replace(/\s/g, "") });
            session.totpEnabled = false;
            toast("两步验证已关闭。");
            close(true);
            onDone();
          } catch (err) {
            error.show(err.message);
          }
        });
      });
      return form;
    },
  });
}

function regenerateRecovery(onDone) {
  const current = passwordInput("current-password", true);
  const error = formError();
  return modal({
    title: "生成新的恢复密钥",
    build: (close) => {
      const submit = h("button", { class: "button button-primary", type: "submit" }, "生成新的恢复密钥");
      const form = h("form", { class: "modal-body", novalidate: true },
        h("p", null, "旧的恢复密钥会立即失效。"), field("当前主密码", current), error.node,
        h("div", { class: "modal-actions" }, h("button", { class: "button", type: "button", onclick: () => close() }, "取消"), submit));
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        error.hide();
        await busy(submit, "正在生成…", async () => {
          try {
            const currentAuth = await proveCurrentPassword(current.value);
            const recovery = await c.newRecovery(session.vaultKey);
            await api("POST", "/recovery", { current_auth_key: currentAuth, recovery_wrapped_vault_key: recovery.wrapped });
            close(true);
            await showRecoveryKey(recovery.text);
            onDone();
          } catch (err) {
            error.show(err.message);
          }
        });
      });
      return form;
    },
  });
}

function rotateVault(ctx, onDone) {
  const current = passwordInput("current-password", true);
  const check = h("input", { type: "checkbox", id: "rotate-confirm" });
  const error = formError();
  const broken = vault.items.filter((item) => !item.payload);
  return modal({
    title: "轮换保险库密钥",
    build: (close) => {
      const submit = h("button", { class: "button button-danger", type: "submit", disabled: true }, "轮换保险库密钥");
      check.addEventListener("change", () => { submit.disabled = !check.checked; });
      const form = h("form", { class: "modal-body", novalidate: true },
        h("p", null, `这个浏览器会生成新的保险库密钥，重新加密全部 ${vault.items.length} 个条目，并生成新的恢复密钥。`),
        h("p", { class: "callout" }, "所有设备令牌都会被删除，每台机器都要重新配置。"),
        broken.length ? h("p", { class: "form-error" }, `有 ${broken.length} 个条目无法解密，轮换前请先删除它们。`) : null,
        field("当前主密码", current),
        h("label", { class: "check" }, check, "我明白所有设备都需要重新配置"),
        error.node,
        h("div", { class: "modal-actions" }, h("button", { class: "button", type: "button", onclick: () => close() }, "取消"), submit));
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        error.hide();
        if (broken.length) return error.show("先删除无法解密的条目。");
        await busy(submit, "正在重新加密…", async () => {
          try {
            const currentAuth = await proveCurrentPassword(current.value);
            const keys = await c.deriveAdminKeys(current.value, session.kdf);
            const newKey = c.newVaultKey();
            const items = await Promise.all(vault.items.map(async (item) => ({
              kind: item.kind, name: item.name, blob: await c.encryptItem(newKey, item.kind, item.name, item.payload) })));
            const wrapped = await c.wrapVaultKey(keys.wrap, newKey);
            keys.wrap.fill(0);
            const recovery = await c.newRecovery(newKey);
            await api("POST", "/rotate", { current_auth_key: currentAuth, items, wrapped_vault_key: wrapped,
              recovery_wrapped_vault_key: recovery.wrapped });
            session.vaultKey.fill(0);
            session.vaultKey = newKey;
            session.wrapped = wrapped;
            await loadItems();
            ctx.setDeviceCount(0);
            close(true);
            toast("保险库密钥已轮换，所有设备令牌已删除。");
            await showRecoveryKey(recovery.text);
            onDone();
          } catch (err) {
            error.show(err.message);
          }
        });
      });
      return form;
    },
  });
}

function describeAgent(ua) {
  if (!ua) return "未知浏览器";
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome"
    : /Safari\//.test(ua) ? "Safari" : "浏览器";
  const os = /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS"
    : /Mac OS X/.test(ua) ? "macOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} · ${os}` : browser;
}

function settingBlock(title, description, action, extra) {
  return h("section", { class: "setting" },
    h("div", { class: "setting-text" }, h("h2", null, title), ...[].concat(description).map((d) => h("p", null, d))),
    action ? h("div", { class: "setting-action" }, action) : null,
    extra || null);
}

export async function renderSettings(page, ctx) {
  const [overview, sessions, status] = await Promise.all([
    api("GET", "/overview"), api("GET", "/sessions"), api("GET", "/status")]);
  if (!ctx.isCurrent()) return;
  const rerender = () => { clear(page); renderSettings(page, ctx); };
  const account = overview.account;

  const signOff = async (s) => {
    await api("DELETE", `/sessions/${s.id}`);
    toast("已让该会话下线。");
    rerender();
  };
  const offButton = (s) => (s.current ? null
    : h("button", { class: "button button-quiet", type: "button", onclick: () => signOff(s) }, "下线"));
  const sessionList = isMobile()
    ? h("ul", { class: "m-list setting-table" }, sessions.sessions.map((s) => m.row({
      head: [h("span", { class: "m-row-title is-plain" }, describeAgent(s.user_agent)),
        s.current ? h("span", { class: "tag tag-ok" }, "当前") : null],
      lines: [m.meta(`登录于 ${ago(s.created_at)}`, `最后活动 ${ago(s.last_seen_at)}`), m.meta(s.ip)],
      quick: offButton(s),
    })))
    : h("div", { class: "table-wrap setting-table" }, h("table", { class: "catalog compact" }, h("tbody", null,
      sessions.sessions.map((s) => h("tr", null,
        h("td", null, describeAgent(s.user_agent), s.current ? h("span", { class: "tag tag-ok" }, "当前") : null),
        h("td", { class: "ledger-ip" }, s.ip || ""),
        h("td", null, "登录于 ", timeTag(s.created_at)),
        h("td", null, "最后活动 ", timeTag(s.last_seen_at)),
        h("td", { class: "cell-actions" }, offButton(s)))))));
  const others = sessions.sessions.filter((s) => !s.current).length;

  page.append(
    h("header", { class: "page-head" }, h("div", null, h("h1", null, "设置"))),
    settingBlock("主密码",
      [`登录后台并解锁保险库都靠它。上次修改于 ${formatDate(account.password_changed_at)}。`],
      h("button", { class: "button", type: "button", onclick: () => changePassword(rerender) }, "修改主密码")),
    settingBlock("两步验证",
      [account.totp_enabled ? "已开启。登录时需要身份验证器上的验证码。" : "未开启。开启后，即使主密码泄露，别人也无法登录后台。"],
      account.totp_enabled
        ? h("button", { class: "button", type: "button", onclick: () => disableTotp(rerender) }, "关闭两步验证")
        : h("button", { class: "button button-primary", type: "button", onclick: () => enableTotp(rerender) }, "开启两步验证")),
    settingBlock("恢复密钥",
      [`忘记主密码时，用它在服务器上重置。当前的恢复密钥生成于 ${formatDate(overview.vault.recovery_set_at)}。`],
      h("button", { class: "button", type: "button", onclick: () => regenerateRecovery(rerender) }, "生成新的恢复密钥")),
    settingBlock("登录会话",
      ["在这些浏览器上登录过后台。闲置 30 分钟或登录满 12 小时后自动失效。"],
      others ? h("button", { class: "button", type: "button", onclick: async () => {
        await api("POST", "/sessions/revoke-others");
        toast("其他会话已全部下线。");
        rerender();
      } }, "让其他会话下线") : null,
      sessionList),
    installMode() ? settingBlock("安装应用",
      ["安装到主屏幕或桌面后，可以像应用一样打开，没有浏览器的地址栏。"],
      h("button", { class: "button", type: "button", onclick: () => install() }, icon("install"), "安装")) : null,
    settingBlock("轮换保险库密钥",
      ["怀疑某个设备令牌泄露、并且想让它以前拿到的密钥也失效时使用。会重新加密所有条目，删除所有设备令牌，并生成新的恢复密钥。",
        overview.vault.vault_rotated_at ? `上次轮换于 ${formatDate(overview.vault.vault_rotated_at)}。` : "还没有轮换过。"],
      h("button", { class: "button button-danger", type: "button", onclick: () => rotateVault(ctx, rerender) }, "轮换保险库密钥")),
    h("section", { class: "setting setting-about" },
      h("div", { class: "setting-text" },
        h("h2", null, "关于"),
        h("p", null, `Egorov ${status.version}，协议 v1。`),
        h("p", null, "服务器能看到：条目的名字、修改时间、设备信息和访问记录。"),
        h("p", null, "服务器看不到：变量的值、函数的内容、备注、主密码和设备令牌。"))));
}

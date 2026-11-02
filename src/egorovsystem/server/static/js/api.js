// JSON calls to /api/v1/admin with the CSRF header and readable errors.

let csrfToken = null;
let onSignedOut = () => {};

export function setCsrf(token) {
  csrfToken = token;
}

export function setSignedOutHandler(handler) {
  onSignedOut = handler;
}

const MESSAGES = {
  invalid_credentials: "用户名或主密码不对。",
  totp_required: "请输入身份验证器上的 6 位验证码。",
  invalid_totp: "验证码不对，或者已经用过了。等下一个验证码再试。",
  rate_limited: "失败次数太多，已暂时锁定。",
  wrong_password: "当前主密码不对。",
  exists: "已经有同名的条目了，换一个名字。",
  not_found: "找不到这个条目，可能已经被删除。刷新后再试。",
  bad_name: "名字只能用字母、数字、下划线、点和连字符，最多 128 个字符，不能以点或连字符开头。",
  bad_scopes: "权限范围的格式不对。每行一个通配规则，例如 openai_* 或 variable:aws_*。",
  bad_expiry: "过期时间必须在未来。",
  too_large: "内容太大了。单个条目最多约 1 MB。",
  items_changed: "轮换期间条目发生了变化。刷新页面后重新轮换。",
  totp_enabled: "两步验证已经开启了。",
  totp_state: "请先完成两步验证的设置步骤。",
  csrf: "会话校验失败。刷新页面后重试。",
  cross_site: "请求被拒绝：它来自其他网站。",
  not_authenticated: "登录已过期，请重新登录。",
  network: "无法连接服务器。检查网络后重试。",
};

export class ApiError extends Error {
  constructor(status, code, data) {
    let message = MESSAGES[code] || (data && data.message) || `服务器返回了错误（${status}）。`;
    if (code === "rate_limited" && data && data.retry_after) {
      message += ` 请在 ${Math.ceil(data.retry_after / 60)} 分钟后再试。`;
    }
    super(message);
    this.status = status;
    this.code = code;
    this.data = data;
  }
}

export async function api(method, path, body) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  let response;
  try {
    response = await fetch(`/api/v1/admin${path}`, {
      method, headers, credentials: "same-origin", cache: "no-store",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", null);
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const code = (data && data.error) || "http";
    if (response.status === 401 && code === "not_authenticated") onSignedOut();
    throw new ApiError(response.status, code, data);
  }
  return data;
}

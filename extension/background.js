// JobCheck 后台：绑定捕获（探测式激活）+ 页面采样。
// 原则：启发式只决定"何时尝试"，平台真实拉取决定"是否成功"；409（未完成登录）不烧凭证、持续重试。

let state = null;
// { token, loginUrl, sessionCookieNames, platformOrigin, sourceTabId, loginTabId, done, lastAttempt }

function setBadge(text, color) {
  chrome.action.setBadgeText({ text });
  if (color) chrome.action.setBadgeBackgroundColor({ color });
}

async function setBindStatus(status) {
  await chrome.storage.session.set({ jcBindStatus: status });
  if (status === "waiting") setBadge("●", "#d89c2e");
  if (status === "activated") setBadge("✓", "#2e7d4f");
  if (status === "failed") setBadge("!", "#c25a5a");
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg && msg.type === "jc.startBind") {
    startBind(msg.payload || {}, sender.tab ? sender.tab.id : null);
    sendResponse({ ok: true });
  } else if (msg && msg.type === "jc.checkNow") {
    if (state && !state.done) {
      state.lastAttempt = 0; // 手动触发绕过防抖
      attemptActivation();
    }
    sendResponse({ ok: true });
  } else if (msg && msg.type === "jc.startSample") {
    const p = msg.payload || {};
    chrome.storage.session.set({
      sampleToken: p.token || null,
      platformOrigin: p.platformOrigin || null,
    });
    // 新采样任务开始即清除上次绑定遗留的终态（activated/failed），
    // 否则 popup 会一直显示旧的「绑定成功/失败」面板，进不了采集面板。
    // remove 幂等；绑定与采样不会同时进行，无需区分当前值。
    chrome.storage.session.remove("jcBindStatus");
    setBadge("●", "#d89c2e");
    sendResponse({ ok: true });
  } else if (msg && msg.type === "jc.sampleNow") {
    submitSampleFromTab(msg.tabId)
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true; // 异步响应
  } else if (msg && msg.type === "jc.syncNow") {
    // 手动「同步当前页」：绕过扩展侧域节流（后端仍有节流/去重兜底）
    syncCurrentTab(msg.tabId)
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  } else if (msg && msg.type === "jc.submitPair") {
    submitPair(String(msg.code || ""))
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  } else if (msg && msg.type === "jc.unpair") {
    unpair()
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;
  } else if (msg && msg.type === "jc.pairState") {
    pairState()
      .then((r) => sendResponse(r))
      .catch(() => sendResponse({ paired: false }));
    return true;
  }
  return false;
});

// ── 绑定捕获 ────────────────────────────────

function startBind(payload, sourceTabId) {
  if (!payload.token || !payload.loginUrl || !payload.platformOrigin) {
    notifySource(false, { detail: "绑定参数不完整" });
    return;
  }
  state = {
    token: payload.token,
    loginUrl: payload.loginUrl,
    sessionCookieNames: payload.sessionCookieNames || [],
    platformOrigin: payload.platformOrigin,
    sourceTabId,
    done: false,
    loginTabId: null,
    lastAttempt: 0,
  };
  setBindStatus("waiting");
  chrome.tabs.create({ url: payload.loginUrl, active: true }, (tab) => {
    if (tab) state.loginTabId = tab.id;
  });
}

chrome.cookies.onChanged.addListener(() => {
  if (state && !state.done) attemptActivation();
});

chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (state && !state.done && tabId === state.loginTabId && info.status === "complete") {
    attemptActivation();
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (state && !state.done && tabId === state.loginTabId) {
    // 登录页被关：允许用户自己开新标签登录，Cookie 事件仍会触发探测
    state.loginTabId = null;
  }
});

async function attemptActivation() {
  if (!state || state.done) return;
  const now = Date.now();
  if (now - state.lastAttempt < 5000) return; // 5 秒防抖
  state.lastAttempt = now;
  try {
    const url = new URL(state.loginUrl);
    let all = await chrome.cookies.getAll({ url: state.loginUrl });
    if (!all.length) all = await chrome.cookies.getAll({ domain: url.hostname });
    if (!all.length) return;

    const names = new Set(all.map((c) => c.name));
    const missing = state.sessionCookieNames.filter((n) => !names.has(n));
    if (missing.length > 0) return; // 已知会话 Cookie 名且未齐：继续等

    const resp = await fetch(state.platformOrigin + "/api/bindings/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: state.token,
        cookies: all.map((c) => ({ name: c.name, value: c.value, domain: c.domain })),
      }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      state.done = true;
      setBindStatus("activated");
      notifySource(true, data);
      if (state.loginTabId != null) {
        chrome.tabs.remove(state.loginTabId).catch(() => {});
      }
    } else if (resp.status === 409) {
      // 未完成登录：不烧凭证，等下一个事件（Cookie 变化 / 页面加载 / 面板手动检查）
      setBindStatus("waiting");
    } else {
      setBindStatus("failed");
      notifySource(false, data);
    }
  } catch (e) {
    setBindStatus("failed");
    notifySource(false, { detail: String(e) });
  }
}

function notifySource(ok, info) {
  if (!state || state.sourceTabId == null) return;
  chrome.tabs
    .sendMessage(state.sourceTabId, { type: "jc.bindResult", ok, info })
    .catch(() => {});
}

// ── 采样：由 popup 触发，采集目标标签页 DOM + 请求-响应对并提交 ──

async function collectSamplePage() {
  // 在页面 MAIN world 执行（才能读到 net-capture.js 的包装缓冲 window.__jcNet）：
  // 裁剪 DOM（去脚本/样式噪声）+ 取 JSON 请求-响应对 + SSR 内嵌数据 + 探测常见记录接口
  const clone = document.documentElement.cloneNode(true);

  // 平衡括号截取（字符串感知）：从 start（指向 { 或 [）截出完整 JSON 片段，
  // 供下方「可执行 JS 内嵌数据」捕获使用；解析由 JSON.parse 把关，截坏即弃
  const extractBalanced = (text, start) => {
    const open = text[start], close = open === "{" ? "}" : "]";
    let depth = 0, inStr = null, esc = false;
    const limit = Math.min(text.length, start + 512 * 1024);
    for (let i = start; i < limit; i++) {
      const c = text[i];
      if (inStr) {
        if (esc) esc = false;
        else if (c === "\\") esc = true;
        else if (c === inStr) inStr = null;
        continue;
      }
      if (c === '"' || c === "'") { inStr = c; continue; }
      if (c === open) depth++;
      else if (c === close) { depth--; if (depth === 0) return text.slice(start, i + 1); }
    }
    return null;
  };

  // SSR 内嵌数据：页面自带的投递数据（「记录页直出、不发列表 XHR」的自研站）。
  // 两类：非执行型 script（type=application/json / text/json）与 可执行 JS 赋值
  // （window.__INITIAL_STATE__ = {...} / var pageData = {...}）。仅作数据素材
  // （url 带 #embedded 标记，后端据此生成 page 型配方，禁止当接口重放）
  const embedded = [];
  for (const s of clone.querySelectorAll('script[type="application/json"], script[type="text/json"]')) {
    const text = (s.textContent || "").trim();
    if ((text.startsWith("{") || text.startsWith("[")) && text.length >= 100) {
      embedded.push({
        url: location.href.split("#")[0] + "#embedded-" + (s.id || embedded.length),
        method: "GET", params: {}, request_body: "", response_body: text.slice(0, 128 * 1024),
      });
    }
    if (embedded.length >= 8) break;
  }
  // 可执行 JS 赋值（≥0.4.13）：仅收 JSON.parse 得动的片段，控制噪声
  if (embedded.length < 8) {
    for (const s of clone.querySelectorAll("script:not([src])")) {
      const t = (s.textContent || "").trim();
      if (t.length < 200 || t.charAt(0) === "{" || t.charAt(0) === "[") continue;
      const m = t.match(/(?:window\.|self\.|var\s+|let\s+|const\s+)?([A-Za-z_$][\w$]*)\s*=\s*([\[{])/);
      if (!m) continue;
      const frag = extractBalanced(t, m.index + m[0].length - 1);
      if (!frag || frag.length < 200) continue;
      try { JSON.parse(frag); } catch { continue; }
      embedded.push({
        url: location.href.split("#")[0] + "#embedded-js-" + m[1],
        method: "GET", params: {}, request_body: "", response_body: frag.slice(0, 128 * 1024),
      });
      if (embedded.length >= 8) break;
    }
  }

  for (const el of clone.querySelectorAll("script,style,noscript,svg,link,meta,template,iframe")) {
    el.remove();
  }
  for (const el of clone.querySelectorAll("*")) {
    const keep = ["id", "class", "href", "type", "placeholder", "title"];
    for (const attr of Array.from(el.attributes)) {
      if (!keep.includes(attr.name)) el.removeAttribute(attr.name);
    }
  }
  let dom = clone.outerHTML;
  if (dom.length > 550000) dom = dom.slice(0, 550000);

  const netActive = !!window.__jcNetInstalled;
  const network = netActive ? (window.__jcNet || []).slice(-40) : [];

  // 主动探测：SSR 站点（记录页不发前端请求）时，用当前登录态只读试探
  // 「应聘记录」接口；命中即按普通网络条目提交，可正常进入配方管线。
  //
  // 飞书招聘（ATSX saas-career）契约，经 2026-09-01 对 hf7l9aiqzx.jobs.feishu.cn
  // 前端 bundle 分析 + 匿名实测验证（非猜测）：
  //   POST /api/v1/search/user/applications，JSON 体 {page_no,page_size}；
  //   必需头 x-csrf-token（= atsx-csrf-token Cookie 值；缺失时服务端回 405，
  //   可匿名 POST /api/v1/csrf/token 刷新，Cookie 7 天有效）+ website-path（站点
  //   路径首段，如 704852）；未登录回 401；GET 会被网关兜底成 HTML，故只用 POST。
  // 其余站点探测同路径：非飞书站自然 404 被过滤，无副作用（只读查询语义）。
  const origin = location.origin;
  const csrfFromCookie = () => {
    const m = document.cookie.match(/(?:^|;\s*)atsx-csrf-token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  };
  const ensureCsrf = async () => {
    let token = csrfFromCookie();
    if (token) return token;
    try {
      const resp = await fetch(origin + "/api/v1/csrf/token", {
        method: "POST", credentials: "include", signal: AbortSignal.timeout(6000),
        headers: { "Content-Type": "application/json" }, body: "{}",
      });
      if (resp.ok) {
        const j = await resp.json().catch(() => null);
        token = (j && j.data && j.data.token) || csrfFromCookie();
      }
    } catch { /* 引导失败不阻断：无 token 的探测会 405，自然被过滤 */ }
    return token;
  };
  const csrfToken = await ensureCsrf();
  const websitePath = location.pathname.split("/")[1] || "";
  const probeSpecs = csrfToken
    ? [
        { path: "/api/v1/search/user/applications", body: { page_no: 1, page_size: 20 } },
        { path: "/api/v1/search/user/applications", body: { page: 1, pageSize: 20 } },
      ]
    : [];
  const known = new Set(network.map((e) => e.url + "|" + e.method));
  const probeResults = await Promise.allSettled(
    probeSpecs.map(async (p) => {
      const resp = await fetch(origin + p.path, {
        method: "POST", credentials: "include", signal: AbortSignal.timeout(6000),
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
          "x-csrf-token": csrfToken,
          "website-path": websitePath,
        },
        body: JSON.stringify(p.body),
      });
      if (!resp.ok) return null;
      const text = await resp.text();
      const t = text.trim();
      if (!t.startsWith("{") && !t.startsWith("[")) return null;
      return {
        url: origin + p.path,
        method: "POST",
        params: {},
        request_body: JSON.stringify(p.body),
        response_body: t.slice(0, 256 * 1024),
        truncated: t.length > 256 * 1024,
      };
    }),
  );
  const probed = [];
  for (const r of probeResults) {
    const e = r.status === "fulfilled" ? r.value : null;
    if (e && e.response_body.length > 80 && !known.has(e.url + "|" + e.method)) {
      known.add(e.url + "|" + e.method); // 两种分页形态都成功时只留第一种
      probed.push(e);
    }
  }

  // 页面实际加载过的接口类资源 URL（含 SSR 场景/未被 JSON 捕获的请求），排障用
  let resources = [];
  try {
    resources = performance
      .getEntriesByType("resource")
      .map((e) => e.name)
      .filter((u) => /^https?:/i.test(u) && !/\.(js|css|png|jpe?g|gif|svg|woff2?|ttf|ico)(\?|$)/i.test(u))
      .slice(-50);
  } catch { /* performance 不可用不阻断 */ }
  return {
    url: location.href,
    dom,
    network: network.concat(probed, embedded).slice(-40),
    netActive,
    probed: probed.length,
    resources,
  };
}

async function submitSampleFromTab(tabId) {
  const { sampleToken, platformOrigin } = (await chrome.storage.session.get([
    "sampleToken",
    "platformOrigin",
  ])) || {};
  if (!sampleToken || !platformOrigin) {
    return { ok: false, error: "没有进行中的采样任务：请先在平台向导点「开始采样」" };
  }
  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: collectSamplePage,
      world: "MAIN",
    });
    if (!result || !result.result) {
      return { ok: false, error: "无法读取该页面（可能是浏览器内部页面）" };
    }
    const page = result.result;
    if (!page.netActive && (page.network || []).length === 0) {
      return {
        ok: false,
        error: "本页在插件安装/更新前就已加载，未能捕获接口数据：请刷新该页后重新点「采集当前页面」",
      };
    }
    const resp = await fetch(platformOrigin + "/api/samples/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: sampleToken,
        url: page.url,
        dom: page.dom,
        resources: page.resources || [],
        network: page.network,
      }),
      signal: AbortSignal.timeout(30000), // 平台无响应时别让弹窗永远停在「采集中…」
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      await chrome.storage.session.remove("sampleToken");
      setBadge("✓", "#2e7d4f");
      return { ok: true };
    }
    setBadge("!", "#c25a5a");
    return {
      ok: false,
      error: typeof data.detail === "string" ? data.detail : "提交失败（HTTP " + resp.status + "）",
    };
  } catch (e) {
    setBadge("!", "#c25a5a");
    return { ok: false, error: String(e) };
  }
}

// ══ v0.5+ 快照链路（REFACTOR_PLAN §3）：配对 / 手动快照 / 上报队列 ══
// 0.6.0 起访问时自动采集移除；与既有绑定/采样流程并存。
// Cookie 永不离开浏览器——采集的只有页面自己拉到的 JSON 响应。

const JC_DEFAULT_API = "http://localhost:5173"; // 走前端代理（与采样提交同路径）；可用 jcApiBase 覆盖

async function jcApiBase() {
  const { jcApiBase } = await chrome.storage.local.get(["jcApiBase"]);
  return jcApiBase || JC_DEFAULT_API;
}

async function getPaired() {
  const { jcDevice } = await chrome.storage.local.get(["jcDevice"]);
  return jcDevice && jcDevice.token ? jcDevice : null;
}

async function submitPair(code) {
  code = String(code || "").trim();
  if (!/^\d{6}$/.test(code)) return { ok: false, error: "请输入 6 位配对码" };
  try {
    const resp = await fetch((await jcApiBase()) + "/api/ext/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, device_label: "浏览器扩展" }),
      signal: AbortSignal.timeout(10000),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      return {
        ok: false,
        error: typeof data.detail === "string" ? data.detail : "配对失败（HTTP " + resp.status + "）",
      };
    }
    await chrome.storage.local.set({
      jcDevice: { token: data.token, email: data.email, pairedAt: Date.now() },
      jcPairError: null,
    });
    flushQueue(); // 配对前积压的快照立即冲刷
    return { ok: true, email: data.email };
  } catch (e) {
    return { ok: false, error: "连不上平台（" + String(e) + "）" };
  }
}

async function unpair() {
  await chrome.storage.local.remove("jcDevice");
  return { ok: true };
}

async function pairState() {
  const paired = await getPaired();
  const stored = (await chrome.storage.local.get(["jcQueue", "jcLastCapture", "jcPairError"])) || {};
  return {
    paired: !!paired,
    email: paired ? paired.email : null,
    queue: (stored.jcQueue || []).length,
    lastCapture: stored.jcLastCapture || null,
    pairError: stored.jcPairError || null,
  };
}

// ── 采集上报 ────────────────────────────────
// 0.6.0 起「访问时自动采集」已移除（多益实盘：仅浏览就自动建档出幻影卡）——
// 数据连接由用户手动建立，来源只有两个：
// 1) 手动「同步当前页」（popup 按钮）：用户在投递记录页显式触发，建立/刷新连接；
// 2) 后台定时回访（jc-autosync）：只回访「已连接站点」，不产生新连接。

// 手动「同步当前页」（popup 按钮触发）：不查域节流，直接采集上报
async function syncCurrentTab(tabId) {
  const paired = await getPaired();
  if (!paired) return { ok: false, error: "请先在面板完成设备配对" };
  if (tabId == null) return { ok: false, error: "找不到目标标签页" };
  let page = null;
  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: collectSnapshotPage,
      world: "MAIN",
    });
    page = result && result.result;
  } catch {
    return { ok: false, error: "无法读取该页面（可能是浏览器内部页面）" };
  }
  if (!page || !(page.network || []).length) {
    return { ok: false, error: "本页没有可采集的数据：请先刷新页面，并确认这是「我的投递」记录页" };
  }
  page.network = scrubPii(page.network);
  const itemId = await enqueueSnapshot({
    url: page.url,
    network: page.network,
    resources: page.resources || [],
    dom: page.dom || "",
    manual: true, // 手动同步豁免后端同站节流（自动采集抢先上报后手动点击不再 429 入队）
    login_suspect: !!page.loginSuspect,
  });
  await flushQueue();
  const out = { ok: true, login_suspect: !!page.loginSuspect };
  // 认领后端解析结果；条目仍在队列（429/5xx 退避）则如实告知已入队
  const stored = (await chrome.storage.local.get(["jcLastCapture", "jcQueue"])) || {};
  const last = stored.jcLastCapture;
  if (last && last.itemId === itemId && last.result) out.result = last.result;
  else if ((stored.jcQueue || []).some((q) => q.id === itemId)) out.queued = true;
  return out;
}

// ── 后台自动同步（REFACTOR_PLAN M2，用户拍板「默认后台自动同步」）──────
// 每小时 tick 从平台拉「已连接站点」清单，轮转回访一个站点：静默开隐藏 tab →
// 等页面 settle（站点自己的 JS 带登录态拉数据）→ 直接采集上报 → 关 tab。
// 每次只回访一个站点（串行错峰防风控）；storage 心跳防止 MV3 SW 等待期被杀。

async function autoSyncTick() {
  // 先落时间戳再干活：SW 启动补跑（autosyncKick）据此判断「最近跑过没有」，
  // 平台不可达的失败 tick 也占一次周期位，避免每分钟唤醒都重试拉站点清单
  await chrome.storage.local.set({ jcLastAutosync: Date.now() });
  const paired = await getPaired();
  if (!paired) return;
  let sites = [];
  try {
    const resp = await fetch((await jcApiBase()) + "/api/ext/sites", {
      headers: { Authorization: "Bearer " + paired.token },
      signal: AbortSignal.timeout(15000),
    });
    if (resp.ok) sites = (await resp.json()).sites || [];
  } catch {
    return; // 平台不可达：下个整点再试
  }
  const usable = sites.filter((s) => /^https?:/i.test(s.url || ""));
  if (!usable.length) return;
  const stored = (await chrome.storage.local.get(["jcAutosyncIndex"])) || {};
  const idx = stored.jcAutosyncIndex || 0;
  const site = usable[idx % usable.length];
  await chrome.storage.local.set({ jcAutosyncIndex: (idx + 1) % usable.length });
  await syncHiddenTab(site.url);
}

async function syncHiddenTab(url) {
  let tabId = null;
  try {
    const tab = await chrome.tabs.create({ url, active: false });
    tabId = tab.id;
    await waitTabComplete(tabId, 20000);
    for (let i = 0; i < 5; i++) await keepAlive(5000); // ~25s settle
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      func: collectSnapshotPage,
      world: "MAIN",
    });
    const page = result && result.result;
    if (page && (page.network || []).length) {
      page.network = scrubPii(page.network);
      await enqueueSnapshot({
        url: page.url,
        network: page.network,
        resources: page.resources || [],
        dom: page.dom || "",
        login_suspect: !!page.loginSuspect,
      });
      await flushQueue();
    }
  } catch {
    // 单站失败忽略（登录态失效等由 login_suspect 数据链路呈现）
  } finally {
    if (tabId != null) {
      try { await chrome.tabs.remove(tabId); } catch { /* 已关闭 */ }
    }
  }
}

function waitTabComplete(tabId, timeoutMs) {
  return new Promise((resolve) => {
    const timer = setTimeout(done, timeoutMs);
    function done() {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }
    function listener(id, info) {
      if (id === tabId && info.status === "complete") done();
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function keepAlive(ms) {
  // 扩展 API 调用会重置 MV3 SW 空闲计时器：等待期定时心跳防被杀
  try { await chrome.storage.local.get(["jcPing"]); } catch { /* ignore */ }
  await new Promise((r) => setTimeout(r, ms));
}

// PII 清洗（上报前剔除常见身份键；值替换为占位符，保持结构可解析）
const JC_PII_KEYS = new Set([
  "name", "realname", "username", "truename", "nickname", "fullname",
  "mobile", "phone", "telephone", "email", "mail",
  "idcard", "idnumber", "applicantguid", "personid", "userid",
]);

function scrubObj(o) {
  if (Array.isArray(o)) return o.map(scrubObj);
  if (o && typeof o === "object") {
    const out = {};
    for (const [k, v] of Object.entries(o)) {
      const n = String(k).toLowerCase().replace(/[^a-z]/g, "");
      out[k] = JC_PII_KEYS.has(n) ? "‹scrubbed›" : scrubObj(v);
    }
    return out;
  }
  return o;
}

function scrubPii(network) {
  return (network || []).map((e) => {
    const body = e.response_body || "";
    if (!body || body.length > 300000) return e; // 过大不重序列化（后端按上限截断）
    try {
      return { ...e, response_body: JSON.stringify(scrubObj(JSON.parse(body))) };
    } catch {
      return e;
    }
  });
}

// ── 上报队列（chrome.storage 持久化；MV3 SW 被杀不丢，退避重试）──────

const JC_QUEUE_MAX = 30;
const JC_RETRY_DELAYS = [60000, 300000, 1800000, 7200000]; // 1min→5min→30min→2h
const JC_MAX_ATTEMPTS = 12;

async function enqueueSnapshot(item) {
  const stored = (await chrome.storage.local.get(["jcQueue"])) || {};
  const queue = stored.jcQueue || [];
  const id = Date.now() + "-" + Math.random().toString(36).slice(2, 8);
  queue.push({
    id,
    attempts: 0,
    nextTryAt: 0,
    ...item,
  });
  while (queue.length > JC_QUEUE_MAX) queue.shift();
  await chrome.storage.local.set({ jcQueue: queue });
  return id; // 调用方（手动同步）凭 id 在 jcLastCapture 里认领解析结果
}

async function flushQueue() {
  const stored = (await chrome.storage.local.get(["jcQueue"])) || {};
  const queue = stored.jcQueue || [];
  if (!queue.length) return;
  const paired = await getPaired();
  if (!paired) return; // 未配对：留在队列，配对后冲刷

  const base = await jcApiBase();
  const now = Date.now();
  for (let i = 0; i < queue.length && i < 5; ) { // 每轮最多冲 5 条，防 SW 长占
    const item = queue[i];
    if (item.nextTryAt > now) { i++; continue; }
    let done = false;
    let retry = false;
    try {
      const resp = await fetch(base + "/api/ext/snapshots", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + paired.token },
        body: JSON.stringify({
          url: item.url,
          network: item.network,
          resources: item.resources || [],
          dom: item.dom || undefined,
          manual: !!item.manual,
          login_suspect: !!item.login_suspect,
        }),
        signal: AbortSignal.timeout(30000),
      });
      if (resp.ok || (resp.status >= 400 && resp.status < 500 && resp.status !== 429)) {
        done = true; // 成功/重复，或 4xx 永久失败（重发无意义）
        if (resp.status === 401) await chrome.storage.local.set({ jcPairError: "设备凭证无效，请重新配对" });
        // 透传后端真实解析结果（parsed/no_data/duplicate + 条数），popup 据此
        // 告知用户，不再用「已上报」掩盖「没识别出数据」（星环 0.5.1 的教训）
        let result = null;
        try {
          if (resp.ok) {
            const data = await resp.json();
            if (data && typeof data.status === "string") {
              result = {
                status: data.status,
                parsed_count: data.parsed_count ?? null,
                created: (data.ingest && data.ingest.created) ?? null,
                note: data.note || null,
              };
            }
          }
        } catch { /* 响应体解析失败不影响队列状态 */ }
        await chrome.storage.local.set({
          jcLastCapture: { url: item.url, at: now, ok: resp.ok, http: resp.status, itemId: item.id, result },
        });
      } else {
        retry = true; // 5xx / 429（节流：该条可能比后端已有的新，等窗口过后再发）
        if (resp.status === 429) item.nextTryAt = now + 11 * 60 * 1000;
      }
    } catch {
      retry = true; // 网络异常：退避重试
    }
    if (done) { queue.splice(i, 1); continue; }
    if (retry) {
      item.attempts = (item.attempts || 0) + 1;
      if (item.attempts >= JC_MAX_ATTEMPTS) { queue.splice(i, 1); continue; }
      if (!item.nextTryAt || item.nextTryAt <= now) {
        item.nextTryAt = now + JC_RETRY_DELAYS[Math.min(item.attempts - 1, JC_RETRY_DELAYS.length - 1)];
      }
      i++;
    }
  }
  await chrome.storage.local.set({ jcQueue: queue });
}

chrome.alarms.create("jc-flush", { periodInMinutes: 1 });
// jc-autosync 守卫式创建：SW 每次唤醒都重跑顶层代码（jc-flush 每分钟唤醒一次），
// 无条件 create 会把 60 分钟周期反复重置导致永不触发——0.5.1 的实盘 bug
chrome.alarms.get("jc-autosync", (existing) => {
  if (!existing) chrome.alarms.create("jc-autosync", { periodInMinutes: 60 });
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "jc-flush") flushQueue();
  else if (alarm.name === "jc-autosync") autoSyncTick();
});
flushQueue(); // SW 启动即冲刷积压
autosyncKick(); // 浏览器重启后闹钟周期重排，启动时补跑欠下的那一轮

// 启动补跑：距上次 tick ≥55min（一个周期内）才补，正常唤醒下是空操作
async function autosyncKick() {
  try {
    const stored = (await chrome.storage.local.get(["jcLastAutosync"])) || {};
    if (Date.now() - (stored.jcLastAutosync || 0) >= 55 * 60 * 1000) await autoSyncTick();
  } catch { /* ignore */ }
}

// ── 影子模式采集器（在页面 MAIN world 执行）────────
// 与 collectSamplePage 的差别：不采 DOM（快照解析只要网络原料）；新增
// 资源回放兜底与已知平台探测（飞书记录页 SSR 直出、初始不发列表 XHR）。

async function collectSnapshotPage() {
  const MAX_BODY = 256 * 1024;
  const netActive = !!window.__jcNetInstalled;
  const network = netActive ? (window.__jcNet || []).slice(-40) : [];

  // SSR 内嵌数据（与采样采集同逻辑：JSON 型 script + 可执行 JS 赋值）
  const extractBalanced = (text, start) => {
    const open = text[start], close = open === "{" ? "}" : "]";
    let depth = 0, inStr = null, esc = false;
    const limit = Math.min(text.length, start + 512 * 1024);
    for (let i = start; i < limit; i++) {
      const c = text[i];
      if (inStr) {
        if (esc) esc = false;
        else if (c === "\\") esc = true;
        else if (c === inStr) inStr = null;
        continue;
      }
      if (c === '"' || c === "'") { inStr = c; continue; }
      if (c === open) depth++;
      else if (c === close) { depth--; if (depth === 0) return text.slice(start, i + 1); }
    }
    return null;
  };
  const embedded = [];
  for (const s of document.querySelectorAll('script[type="application/json"], script[type="text/json"]')) {
    const text = (s.textContent || "").trim();
    if ((text.startsWith("{") || text.startsWith("[")) && text.length >= 100) {
      embedded.push({
        url: location.href.split("#")[0] + "#embedded-" + (s.id || embedded.length),
        method: "GET", params: {}, request_body: "", response_body: text.slice(0, MAX_BODY),
      });
    }
    if (embedded.length >= 8) break;
  }
  if (embedded.length < 8) {
    for (const s of document.querySelectorAll("script:not([src])")) {
      const t = (s.textContent || "").trim();
      if (t.length < 200 || t.charAt(0) === "{" || t.charAt(0) === "[") continue;
      const m = t.match(/(?:window\.|self\.|var\s+|let\s+|const\s+)?([A-Za-z_$][\w$]*)\s*=\s*([\[{])/);
      if (!m) continue;
      const frag = extractBalanced(t, m.index + m[0].length - 1);
      if (!frag || frag.length < 200) continue;
      try { JSON.parse(frag); } catch { continue; }
      embedded.push({
        url: location.href.split("#")[0] + "#embedded-js-" + m[1],
        method: "GET", params: {}, request_body: "", response_body: frag.slice(0, MAX_BODY),
      });
      if (embedded.length >= 8) break;
    }
  }

  const origin = location.origin;
  const known = new Set(network.concat(embedded).map((e) => e.url.split("?")[0] + "|" + e.method));
  const pushEntry = (e) => {
    const key = e.url.split("?")[0] + "|" + e.method;
    if (!known.has(key)) { known.add(key); network.push(e); }
  };

  // 已知平台探测（飞书 ATSX 契约，与采样采集同规格；非飞书站 404/405 自然被过滤）
  const csrfFromCookie = () => {
    const m = document.cookie.match(/(?:^|;\s*)atsx-csrf-token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  };
  const ensureCsrf = async () => {
    let token = csrfFromCookie();
    if (token) return token;
    try {
      const resp = await fetch(origin + "/api/v1/csrf/token", {
        method: "POST", credentials: "include", signal: AbortSignal.timeout(6000),
        headers: { "Content-Type": "application/json" }, body: "{}",
      });
      if (resp.ok) {
        const j = await resp.json().catch(() => null);
        token = (j && j.data && j.data.token) || csrfFromCookie();
      }
    } catch { /* 引导失败不阻断：无 token 的探测会 405，自然被过滤 */ }
    return token;
  };
  const csrfToken = await ensureCsrf();
  const websitePath = location.pathname.split("/")[1] || "";
  if (csrfToken) {
    for (const body of [{ page_no: 1, page_size: 20 }, { page: 1, pageSize: 20 }]) {
      try {
        const resp = await fetch(origin + "/api/v1/search/user/applications", {
          method: "POST", credentials: "include", signal: AbortSignal.timeout(6000),
          headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "x-csrf-token": csrfToken,
            "website-path": websitePath,
          },
          body: JSON.stringify(body),
        });
        if (!resp.ok) break; // 401/405：非飞书站或未登录，不试第二种分页形态
        const text = await resp.text();
        const t = text.trim();
        if (t.startsWith("{") || t.startsWith("[")) {
          pushEntry({
            url: origin + "/api/v1/search/user/applications",
            method: "POST", params: {},
            request_body: JSON.stringify(body),
            response_body: t.slice(0, MAX_BODY),
            truncated: t.length > MAX_BODY,
          });
          break;
        }
      } catch { /* 探测失败忽略 */ }
    }
  }

  // 资源回放兜底：页面加载过但未进缓冲的接口类资源（wrapper 缺席/缓存/SSR 场景）
  let resources = [];
  try {
    resources = performance
      .getEntriesByType("resource")
      .map((e) => e.name)
      .filter((u) => /^https?:/i.test(u) && !/\.(js|css|png|jpe?g|gif|svg|woff2?|ttf|ico)(\?|$)/i.test(u))
      .slice(-50);
  } catch { /* performance 不可用不阻断 */ }
  const REPLAY_PATH_RE = /(application|deliver|submission|apply[-_/]?record|personal[-_/]?center|progress|candidate)/i;
  const replayCands = [];
  for (const u of resources) {
    try {
      const x = new URL(u);
      const bare = x.origin + x.pathname;
      if (x.origin !== origin) continue;
      if (!REPLAY_PATH_RE.test(x.pathname)) continue;
      if (known.has(bare + "|GET") || known.has(bare + "|POST")) continue;
      if (replayCands.some((c) => c.bare === bare)) continue;
      replayCands.push({ url: u, bare });
    } catch { /* 坏 URL 跳过 */ }
    if (replayCands.length >= 5) break;
  }
  for (const cand of replayCands) {
    // GET→POST{} 递进：Moka 类接口 GET→405（2026-09-01 实盘证伪纯 GET）；
    // 空 JSON 体 POST 是已知安全形态（北森 GetAllDeliveryRecord 契约）
    for (const spec of [{ method: "GET", body: null }, { method: "POST", body: "{}" }]) {
      try {
        const resp = await fetch(cand.url, {
          method: spec.method,
          credentials: "include",
          signal: AbortSignal.timeout(6000),
          ...(spec.body ? { headers: { "Content-Type": "application/json" }, body: spec.body } : {}),
        });
        if (!resp.ok) continue;
        const text = await resp.text();
        const t = text.trim();
        if (!t.startsWith("{") && !t.startsWith("[")) break; // 非 JSON：该候选放弃
        if (t.length > 80) {
          pushEntry({
            url: cand.bare,
            method: spec.method,
            params: {},
            request_body: spec.body || "",
            response_body: t.slice(0, MAX_BODY),
            truncated: t.length > MAX_BODY,
          });
          break;
        }
      } catch { /* 单候选失败忽略 */ }
    }
  }

  // 疑似未登录：URL 形态 + 响应体标记（后端据此标连接 stale，前端提醒重访）
  const LOGIN_URL_RE = /(login|signin|passport)/i;
  const loginSuspect =
    LOGIN_URL_RE.test(location.pathname) ||
    network.some((e) =>
      /not login|请登录|未登录|SESSION_INVALID/i.test((e.response_body || "").slice(0, 400))
    );

  // 裁剪 DOM（v0.5.5 兜底原料）：网络三层钩子（fetch/XHR、JSON.parse、Response.json）
  // 都拿不到明文时（星环实盘：解密在 Web Worker；网易实盘：传输不明零捕获），
  // 渲染出来的记录一定在 DOM 里，后端按「同签名重复兄弟行」提取。剔除脚本样式
  // 与非结构属性，控制体积与噪声。
  let dom = "";
  try {
    const clone = document.documentElement.cloneNode(true);
    for (const el of clone.querySelectorAll(
      "script,style,noscript,svg,link,meta,template,iframe,canvas,img,video,audio"
    )) {
      el.remove();
    }
    const keepAttrs = new Set(["id", "class", "href", "type", "title"]);
    for (const el of clone.querySelectorAll("*")) {
      for (const attr of Array.from(el.attributes)) {
        if (!keepAttrs.has(attr.name)) el.removeAttribute(attr);
      }
    }
    dom = clone.outerHTML.slice(0, 400000);
  } catch { /* DOM 失败不阻断网络原料上报 */ }

  return {
    url: location.href,
    network: network.concat(embedded).slice(-40),
    netActive,
    resources,
    dom,
    loginSuspect,
  };
}

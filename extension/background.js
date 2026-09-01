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
    setBadge("●", "#d89c2e");
    sendResponse({ ok: true });
  } else if (msg && msg.type === "jc.sampleNow") {
    submitSampleFromTab(msg.tabId)
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true; // 异步响应
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

// ── 采样：由 popup 触发，采集目标标签页 DOM + XHR 清单并提交 ──

function collectSamplePage() {
  // 在页面上下文执行：裁剪 DOM（去掉脚本/样式等噪声，保留结构与文本），收集 XHR/fetch 清单
  const clone = document.documentElement.cloneNode(true);
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
  const resources = [
    ...new Set(
      performance
        .getEntriesByType("resource")
        .filter((e) => e.initiatorType === "xmlhttprequest" || e.initiatorType === "fetch")
        .map((e) => e.name),
    ),
  ].slice(0, 50);
  return { url: location.href, dom, resources };
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
    });
    if (!result || !result.result) {
      return { ok: false, error: "无法读取该页面（可能是浏览器内部页面）" };
    }
    const resp = await fetch(platformOrigin + "/api/samples/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: sampleToken,
        url: result.result.url,
        dom: result.result.dom,
        resources: result.result.resources,
      }),
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

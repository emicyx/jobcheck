// JobCheck 插件操作面板：点图标必达此面板，杜绝"点了没反应"。
const PLATFORM_DEFAULT = "http://localhost:5173";

const app = document.getElementById("app");
// 面板顶部显示版本号：用户可一眼确认加载的是否为新代码（旧版本是排障第一步）
document.getElementById("ver").textContent = "v" + chrome.runtime.getManifest().version;

function h(html) {
  app.innerHTML = html;
  return app;
}

async function state() {
  const { sampleToken, platformOrigin } = (await chrome.storage.session.get([
    "sampleToken",
    "platformOrigin",
  ])) || {};
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return { token: sampleToken, platform: platformOrigin || PLATFORM_DEFAULT, tab: tab || null };
}

function altOrigin(origin) {
  // localhost 与 127.0.0.1 视为同一平台（用户可能用任一形式打开过平台页）
  if (origin.includes("//127.0.0.1")) return origin.replace("//127.0.0.1", "//localhost");
  if (origin.includes("//localhost")) return origin.replace("//localhost", "//127.0.0.1");
  return origin;
}

// 手动同步结果文案：透传后端真实解析结果（parsed/no_data/duplicate），
// 不再用「已上报」一句话掩盖「没识别出数据」（星环 0.5.1 实盘教训）
function syncResultText(resp) {
  if (!resp || !resp.ok) return (resp && resp.error) || "同步失败";
  if (resp.login_suspect) return "✓ 已上报（疑似未登录，建议在网站里重新登录一次）";
  const r = resp.result;
  if (r) {
    if (r.status === "parsed") return `✓ 已识别 ${r.parsed_count ?? "?"} 条投递记录，稍后在看板查看`;
    if (r.status === "duplicate") {
      const created = r.created ?? 0;
      return created > 0
        ? `✓ 数据与上次一致，补建了 ${created} 张缺失的卡片`
        : "✓ 数据与上次一致，看板无需更新";
    }
    if (r.status === "no_data")
      return "已上报，但未识别到投递数据：请确认停留在「我的投递」记录页并刷新后重试；也可先在看板手动记录";
    return `已上报（${r.status}）`;
  }
  if (resp.queued) return "已入队（上报频繁/网络波动），稍后自动重试";
  return "✓ 已上报，稍后在看板查看";
}

function bindOpen(platform) {
  // 「打开平台」按钮在部分分支不渲染（如当前已在平台页），此时无需绑定
  const btn = document.getElementById("open-platform");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    // 优先回到已打开的平台标签页（向导就在那里），没有才开新页
    try {
      const origins = [platform];
      const alt = altOrigin(platform);
      if (alt !== platform) origins.push(alt);
      let tabs = [];
      for (const o of origins) {
        tabs = tabs.concat(await chrome.tabs.query({ url: o + "/*" }));
      }
      // 取 id 最大（最近打开）的那个平台标签页
      const tab = tabs.length ? tabs.reduce((a, b) => (b.id > a.id ? b : a)) : null;
      if (tab) {
        await chrome.tabs.update(tab.id, { active: true });
        if (tab.windowId != null) await chrome.windows.update(tab.windowId, { focused: true });
      } else {
        await chrome.tabs.create({ url: platform });
      }
    } catch {
      await chrome.tabs.create({ url: platform });
    }
    window.close();
  });
}

async function init() {
  const { token, platform, tab } = await state();
  const { jcBindStatus } = (await chrome.storage.session.get(["jcBindStatus"])) || {};
  const url = tab && tab.url || "";
  const isHttp = /^https?:/i.test(url);
  const isPlatform = url.startsWith(platform);
  let host = "";
  try { host = new URL(url).host; } catch { /* 非 http 页 */ }

  // ── 绑定任务进行中：优先显示绑定面板 ──
  if (jcBindStatus === "waiting") {    h(`
      <p><b>绑定进行中</b></p>
      <p class="muted">正在等待你在官网完成登录。登录成功后自动完成绑定并同步投递，平台页面会自动更新。</p>
      <p class="muted">当前页：${host || "（无）"}。若你已登录完毕却没动静，点下面按钮手动触发一次检查。</p>
      <button id="check-now">立即检查</button>
      <p class="muted" style="margin-top:8px">检查无果时：确认当前浏览器登录的正是你投递用的官网账号。</p>
    `);
    document.getElementById("check-now").addEventListener("click", async (e) => {
      e.target.disabled = true;
      e.target.textContent = "检查中…";
      await chrome.runtime.sendMessage({ type: "jc.checkNow" });
      setTimeout(init, 2000);
    });
    return;
  }
  // ── 有采样凭证：采样面板优先（历史绑定的 activated/failed 终态不得挡住新采样任务）──
  if (token) {
    if (!isHttp || isPlatform) {
      h(`
      <p>采样已就绪，但当前页面不能采集${host ? `（${host}）` : ""}。</p>
      <p class="muted">请切换到目标招聘网站的<b>「我的投递 / 应聘进度」</b>页面，再点本图标。</p>
    `);
      return;
    }

    // 页面形态预检：职位详情/投递表单页上没有列表数据，采了也注定生成失败
    let kindTip = "";
    try {
      const path = new URL(url).pathname.toLowerCase();
      const listLike = /(^|\/)(mine|my|center|apply|applies|application|applications|deliver|progress|record|list)([\/._-]|$)/.test(path);
      // 只对高置信度的详情页报警（/position/12345 形态）；
      // 飞书招聘等站点的记录页路径含门户 ID（如 /704852/position/application），不能误判
      const formLike = /\/(position|job|vacancy|detail)s?\/\d+/.test(path);
      if (listLike) {
        kindTip = `<p class="ok">✓ 当前页看起来就是投递列表页</p>`;
      } else if (formLike) {
        kindTip = `<div class="warn-box">当前页看起来是<b>职位详情 / 投递表单页</b>：这里没有你的投递列表，采集了也无法生成配置。请先进入网站的「我的投递 / 应聘进度」页（能看到多条投递记录）再采集。</div>`;
      }
    } catch { /* URL 解析失败不拦采集 */ }
    h(`
    <p>即将采集 <b>${host}</b> 当前页数据（裁剪后的页面结构 + 接口地址清单）。</p>
    <p class="muted">请确认这已经是「我的投递」列表页，且你的投递记录可见。</p>
    ${kindTip}
    <button id="do-sample">采集当前页面</button>
  `);
    document.getElementById("do-sample").addEventListener("click", async (e) => {
      e.target.disabled = true;
      e.target.textContent = "采集中…";
      let resp;
      try {
        resp = await chrome.runtime.sendMessage({ type: "jc.sampleNow", tabId: tab.id });
      } catch (err) {
        resp = { ok: false, error: String(err) };
      }
      if (resp && resp.ok) {
        h(`
        <p class="ok">✓ 采样已提交</p>
        <p class="muted">回到平台向导，点「我已采集，确认」即可。</p>
        <button id="open-platform" class="secondary">回到平台</button>
      `);
        bindOpen(platform);
      } else {
        h(`
        <p class="err">采集失败</p>
        <p class="muted">${(resp && resp.error) || "未知错误"}</p>
        <div class="warn-box">常见原因：采样凭证已过期（30 分钟）——回平台重新点「开始采样」。</div>
        <button id="retry">重试</button>
      `);
        document.getElementById("retry").addEventListener("click", init);
      }
    });
    return;
  }

  // ── 历史绑定的终态提示（无采样任务时才显示）──
  if (jcBindStatus === "activated") {
    h(`
      <p class="ok">✓ 绑定成功</p>
      <p class="muted">投递记录已同步到看板，回到平台查看。</p>
      <button id="open-platform" class="secondary">回到平台</button>
    `);
    bindOpen(platform);
    return;
  }
  if (jcBindStatus === "failed") {
    h(`
      <p class="err">绑定失败</p>
      <p class="muted">请在平台向导里点「重新发起绑定」重试。</p>
      <button id="open-platform">打开平台</button>
    `);
    bindOpen(platform);
    return;
  }

  // ── 没有进行中的采样凭证 ──
  h(`
    <p>当前没有进行中的采样任务。</p>
    <p class="muted">先在平台的「接入追踪」向导里点<b>「开始采样」</b>，再回到目标网站的「我的投递」页点本图标。</p>
    ${isPlatform ? "" : `<button id="open-platform">打开平台</button>`}
    <p class="muted" style="margin-top:10px">若刚装/刚更新插件：请刷新平台页面后再试。</p>
    <div class="pair-box" id="pair-mount"></div>
  `);
  bindOpen(platform);
  renderPairBox();
}

// ── 设备配对：配对后由用户手动「同步当前页」建立数据连接 ──
async function renderPairBox() {
  const mount = document.getElementById("pair-mount");
  if (!mount) return;
  let st = null;
  try {
    st = await chrome.runtime.sendMessage({ type: "jc.pairState" });
  } catch {
    return; // 后台未就绪：不渲染
  }
  if (!st) return;

  if (st.paired) {
    const last = st.lastCapture;
    mount.innerHTML = `
      <p class="ok" style="margin:0">✓ 已配对</p>
      <p class="muted" style="margin:2px 0 0">${st.email || ""}${st.queue ? ` · 待上报 ${st.queue} 条` : ""}</p>
      ${last ? `<p class="muted" style="margin:2px 0 0">最近采集：${new Date(last.at).toLocaleTimeString()} ${last.ok ? "✓" : "（HTTP " + last.http + "）"}</p>` : ""}
      <p class="muted" style="margin:2px 0 0">到招聘站「我的投递」页点「同步当前页」建立连接；已连接站点每小时自动刷新。</p>
      ${st.pairError ? `<p class="err" style="margin:4px 0 0">${st.pairError}</p>` : ""}
      <button id="sync-now">同步当前页</button>
      <p class="muted" id="sync-result" style="margin:6px 0 0;display:none"></p>
      <button id="unpair" class="secondary">解除配对</button>
    `;
    document.getElementById("sync-now").addEventListener("click", async (e) => {
      const btn = e.target;
      const resultEl = document.getElementById("sync-result");
      btn.disabled = true;
      btn.textContent = "采集中…";
      resultEl.style.display = "none";
      try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        let resp;
        try {
          resp = await chrome.runtime.sendMessage({ type: "jc.syncNow", tabId: tab && tab.id });
        } catch (err) {
          resp = { ok: false, error: String(err) };
        }
        resultEl.textContent = syncResultText(resp);
        resultEl.className =
          resp && resp.ok && !(resp.result && resp.result.status === "no_data") ? "muted" : "err";
      } finally {
        resultEl.style.display = "block";
        btn.disabled = false;
        btn.textContent = "同步当前页";
      }
    });
    document.getElementById("unpair").addEventListener("click", async () => {
      await chrome.runtime.sendMessage({ type: "jc.unpair" });
      renderPairBox();
    });
    return;
  }

  mount.innerHTML = `
    <p style="margin:0"><b>设备配对</b> <span class="muted">（v0.5 影子模式）</span></p>
    <p class="muted" style="margin:2px 0 0">配对后，到招聘站「我的投递」页点本图标 →「同步当前页」即可建立数据连接（Cookie 不离开浏览器）。配对码在平台「设置 → 扩展同步」页生成：</p>
    <p class="muted" style="margin:4px 0 0"><code>fetch('/api/ext/pair-code',{method:'POST',credentials:'include'}).then(r=>r.json()).then(console.log)</code></p>
    <div class="pair-row">
      <input id="pair-code" maxlength="6" inputmode="numeric" placeholder="配对码" />
      <button id="do-pair">配对</button>
    </div>
    <p class="err" id="pair-err" style="margin:6px 0 0;display:none"></p>
  `;
  const errEl = document.getElementById("pair-err");
  const doPair = async () => {
    const btn = document.getElementById("do-pair");
    const code = document.getElementById("pair-code").value;
    btn.disabled = true;
    btn.textContent = "配对中…";
    errEl.style.display = "none";
    let resp;
    try {
      resp = await chrome.runtime.sendMessage({ type: "jc.submitPair", code });
    } catch (e) {
      resp = { ok: false, error: String(e) };
    }
    if (resp && resp.ok) {
      renderPairBox();
      return;
    }
    btn.disabled = false;
    btn.textContent = "配对";
    errEl.textContent = (resp && resp.error) || "未知错误";
    errEl.style.display = "block";
  };
  document.getElementById("do-pair").addEventListener("click", doPair);
  document.getElementById("pair-code").addEventListener("keydown", (e) => {
    if (e.key === "Enter") doPair();
  });
}

init();

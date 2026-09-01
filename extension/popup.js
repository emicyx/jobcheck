// JobCheck 插件操作面板：点图标必达此面板，杜绝"点了没反应"。
const PLATFORM_DEFAULT = "http://localhost:5173";

const app = document.getElementById("app");

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

function bindOpen(platform) {
  document.getElementById("open-platform").addEventListener("click", () => {
    chrome.tabs.create({ url: platform });
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
  if (jcBindStatus === "waiting") {
    h(`
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
  if (!token) {
    h(`
      <p>当前没有进行中的采样任务。</p>
      <p class="muted">先在平台的「接入追踪」向导里点<b>「开始采样」</b>，再回到目标网站的「我的投递」页点本图标。</p>
      ${isPlatform ? "" : `<button id="open-platform">打开平台</button>`}
      <p class="muted" style="margin-top:10px">若刚装/刚更新插件：请刷新平台页面后再试。</p>
    `);
    bindOpen(platform);
    return;
  }

  // ── 有凭证，但当前页不可采集 ──
  if (!isHttp || isPlatform) {
    h(`
      <p>采样已就绪，但当前页面不能采集${host ? `（${host}）` : ""}。</p>
      <p class="muted">请切换到目标招聘网站的<b>「我的投递 / 应聘进度」</b>页面，再点本图标。</p>
    `);
    return;
  }

  // ── 就绪：提供采集按钮 ──
  h(`
    <p>即将采集 <b>${host}</b> 当前页数据（裁剪后的页面结构 + 接口地址清单）。</p>
    <p class="muted">请确认这已经是「我的投递」列表页，且你的投递记录可见。</p>
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
}

init();

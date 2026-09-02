// JobCheck 平台页 ↔ 扩展 的消息中继（content script 仅注入平台自身域名）
//
// 关键防护：扩展在 chrome://extensions 被重新加载/更新后，已注入本页的旧
// 内容脚本会与后台断开（chrome.runtime 调用抛 "Extension context invalidated"）。
// 此时旧脚本必须：
//   1) 不再应答 jc.ping —— 让向导判定「插件未就绪」并提示刷新，而不是假装在线
//      （否则向导点「开始采样」后凭证交不到新后台，只会一直等回执）；
//   2) 不再向后台转发消息，改为回 jc.contextInvalidated，让页面给出精确提示。
// 刷新本页后新脚本注入，一切自然恢复。

function runtimeAlive() {
  try {
    return !!(chrome.runtime && chrome.runtime.id);
  } catch {
    return false;
  }
}

function notifyStale() {
  window.postMessage({ source: "jobcheck-ext", type: "jc.contextInvalidated" }, "*");
}

function sendToBackground(type, payload, onDone) {
  try {
    chrome.runtime.sendMessage({ type, payload }, () => {
      if (chrome.runtime.lastError) return; // 无接收方等错误：静默，等待页面侧兜底轮询
      onDone();
    });
  } catch {
    notifyStale();
  }
}

window.addEventListener("message", (ev) => {
  if (ev.source !== window) return;
  const msg = ev.data;
  if (!msg || msg.source !== "jobcheck-page") return;

  if (!runtimeAlive()) {
    // 旧桥已断：ping 不应答（表现为插件未就绪），动作消息回失效事件
    if (msg.type !== "jc.ping") notifyStale();
    return;
  }

  if (msg.type === "jc.ping") {
    window.postMessage({ source: "jobcheck-ext", type: "jc.pong" }, "*");
    return;
  }
  if (msg.type === "jc.startBind" || msg.type === "jc.checkNow") {
    sendToBackground(msg.type, msg.payload, () => {
      if (msg.type === "jc.startBind") {
        window.postMessage({ source: "jobcheck-ext", type: "jc.bindArmed" }, "*");
      }
    });
  } else if (msg.type === "jc.startSample") {
    sendToBackground(msg.type, msg.payload, () => {
      window.postMessage({ source: "jobcheck-ext", type: "jc.sampleArmed" }, "*");
    });
  }
});

try {
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "jc.bindResult") {
      window.postMessage(
        { source: "jobcheck-ext", type: "jc.bindResult", ok: msg.ok, info: msg.info },
        "*"
      );
    }
  });
} catch {
  // 注入瞬间即失效（极少见）：本页等刷新后由新脚本接管
}

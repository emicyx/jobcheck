// JobCheck 平台页 ↔ 扩展 的消息中继（content script 仅注入平台自身域名）
window.addEventListener("message", (ev) => {
  if (ev.source !== window) return;
  const msg = ev.data;
  if (!msg || msg.source !== "jobcheck-page") return;

  if (msg.type === "jc.ping") {
    window.postMessage({ source: "jobcheck-ext", type: "jc.pong" }, "*");
    return;
  }
  if (msg.type === "jc.startBind" || msg.type === "jc.checkNow") {
    chrome.runtime.sendMessage({ type: msg.type, payload: msg.payload }, (resp) => {
      if (chrome.runtime.lastError) return;
      if (msg.type === "jc.startBind") {
        window.postMessage({ source: "jobcheck-ext", type: "jc.bindArmed" }, "*");
      }
    });
  } else if (msg.type === "jc.startSample") {
    chrome.runtime.sendMessage({ type: msg.type, payload: msg.payload }, (resp) => {
      if (chrome.runtime.lastError) return;
      window.postMessage({ source: "jobcheck-ext", type: "jc.sampleArmed" }, "*");
    });
  }
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "jc.bindResult") {
    window.postMessage(
      { source: "jobcheck-ext", type: "jc.bindResult", ok: msg.ok, info: msg.info },
      "*"
    );
  }
});

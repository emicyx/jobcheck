// JobCheck 网络捕获（MAIN world，document_start）：包装 fetch/XHR 记录请求-响应对。
// 数据最小化：缓冲只存在于当前页面 JS 上下文，不上传不落盘；页面关闭即消失。
// 仅记录 JSON 响应与必要元数据，不做任何注入或修改页面行为。
//
// 0.6.0 起「访问时自动采集」已移除（用户手动快照建档）：本脚本只负责缓冲，
// 由手动「同步当前页」与后台定时回访采集读取；检测信号（jc.listDetected）不再发送。
(function () {
  if (window.__jcNetInstalled) return;
  window.__jcNetInstalled = true;

  const MAX_ENTRIES = 60;
  const MAX_BODY = 256 * 1024; // v0.4.13：128→256KB，自研站列表常内联 JD 全文易超限
  const SKIP_RE = /(\.js|\.css|\.png|\.jpg|\.jpeg|\.gif|\.svg|\.woff2?|\.ttf|\.map)(\?|$)|\/(log|track|beacon|analytics|sentry|monitor)(\/|$)/i;
  const buf = [];
  window.__jcNet = buf;

  function paramDict(url) {
    try {
      const out = {};
      for (const [k, v] of new URL(url).searchParams) out[k] = v;
      return out;
    } catch { return {}; }
  }

  function push(entry) {
    try {
      if (!/^https?:/i.test(entry.url) || SKIP_RE.test(entry.url)) return;
      // 同 URL 去重，保留最后一次（如轮询刷新）
      const i = buf.findIndex((e) => e.url === entry.url && e.method === entry.method);
      if (i >= 0) buf.splice(i, 1);
      buf.push(entry);
      if (buf.length > MAX_ENTRIES) buf.shift();
    } catch { /* 缓冲操作失败不影响页面 */ }
  }

  function looksJson(text) {
    const c = (text || "").trim().charAt(0);
    return c === "{" || c === "[";
  }

  // 投递页形态判定（仅用于限制解密钩子的探测范围，不再触发任何上报）
  const PAGE_URL_RE = /(application|deliver|apply|record|personal[-_/]?center|progress|candidate|\/mine)/i;

  // ── JSON.parse / Response.json 包装（v0.5.4）：多槽位捕获「页面内解密」的明文 ──
  // 背景：Moka 类站点接口响应加密（{"data":密文,"necromancer":…}），明文只在页面
  // 解密后才出现。实盘教训（星环快照 #3/#4）：
  // ① 0.5.2 英文 title/status 词典预判会漏未知键形（可能中文键）；
  // ② 单一 #decrypted 槽位被后解析的大对象顶掉（站点配置的 jobs 职位数组冒充投递）；
  // ③ 「数组 ≥2 个字典」的门会漏单条投递（用户只投过 1 个岗位时 list 长度为 1）；
  // ④ 部分代码路径走原生 resp.json()（C++ 解析，不经 JSON.parse）。
  // 因此：形状门降到「≥1 个字典、≥3 键的数组」，另钩 Response.json 兜原生解析，
  // 内容哈希分槽保留最近 6 个不同对象——「像不像投递」全部交给后端 heuristics
  // （支持中文键 + 职位广告键过滤）。钩子只读旁听：不改返回值、永不抛错、
  // 仅投递页 URL 才探测。
  const DECRYPTED_SLOTS = 6;
  const decryptedLru = []; // 内容哈希标签，旧→新
  const origParse = JSON.parse;
  JSON.parse = function (text, reviver) {
    const value = origParse.apply(this, arguments);
    try {
      if (value && typeof value === "object" && PAGE_URL_RE.test(location.href)) {
        captureDecrypted(value);
      }
    } catch { /* 钩子失败不影响页面 */ }
    return value;
  };

  function captureDecrypted(value) {
    // 宽松门 + 节点预算：热路径上每次解析都会过这里，
    // 大对象浅尝辄止，超预算视为噪声，保证开销有上界
    let budget = 1500;
    const looseList = (node, depth) => {
      if (!node || typeof node !== "object" || depth > 5 || budget <= 0) return false;
      if (Array.isArray(node)) {
        // ≥1 个字典即算候选（单条投递记录的 list 也有效），每个字典 ≥3 键防噪声
        const dicts = node.filter((x) => x && typeof x === "object" && !Array.isArray(x));
        if (dicts.length >= 1 && Object.keys(dicts[0]).length >= 3) return true;
        for (const x of node) {
          budget -= 1;
          if (looseList(x, depth + 1)) return true;
        }
        return false;
      }
      for (const v of Object.values(node)) {
        budget -= 1;
        if (looseList(v, depth + 1)) return true;
      }
      return false;
    };
    if (!looseList(value, 0)) return;
    let text = null;
    try { text = JSON.stringify(value); } catch { return; }
    if (!text) return;
    // 内容前缀哈希做槽位键：同对象重解析只刷新 LRU 位次，不同对象各占一槽
    let hash = 0;
    const scan = Math.min(text.length, 4096);
    for (let i = 0; i < scan; i++) hash = (hash * 31 + text.charCodeAt(i)) | 0;
    const tag = (hash >>> 0).toString(36);
    const at = decryptedLru.indexOf(tag);
    if (at >= 0) decryptedLru.splice(at, 1);
    decryptedLru.push(tag);
    while (decryptedLru.length > DECRYPTED_SLOTS) {
      const oldest = decryptedLru.shift();
      const j = buf.findIndex((e) => String(e.url).includes("#decrypted-" + oldest));
      if (j >= 0) buf.splice(j, 1);
    }
    push({
      url: location.href + "#decrypted-" + tag,
      method: "GET",
      params: {},
      request_body: "",
      response_body: text.slice(0, MAX_BODY),
      truncated: text.length > MAX_BODY,
    });
  }

  // 原生解析兜底：resp.json() 在 C++ 侧解析、不经上面的 JSON.parse 包装，
  // 解密后改走 new Response(明文).json() 或直接 json() 的路径会漏——单独包一层
  const origJson = Response.prototype.json;
  if (origJson) {
    Response.prototype.json = function () {
      return origJson.apply(this, arguments).then((value) => {
        try {
          if (value && typeof value === "object" && PAGE_URL_RE.test(location.href)) {
            captureDecrypted(value);
          }
        } catch { /* 钩子失败不影响页面 */ }
        return value;
      });
    };
  }

  // ── fetch 包装 ──
  const origFetch = window.fetch;
  if (origFetch) {
    window.fetch = function (input, init) {
      try {
        const url = typeof input === "string" ? input : (input && input.url) || "";
        const method = ((init && init.method) || (input && input.method) || "GET").toUpperCase();
        let requestBody = "";
        const rawBody = init && init.body;
        if (typeof rawBody === "string" && rawBody.length < 4096) requestBody = rawBody;
        return origFetch.apply(this, arguments).then((resp) => {
          try {
            // 不看 content-type：有的接口用 text/plain 甚至不带 CT 返回 JSON
            // （XHR 包装同样只看内容）；looksJson 兜底，非 JSON 内容不记录
            resp.clone().text().then((text) => {
              if (looksJson(text)) {
                push({ url: resp.url || url, method, params: paramDict(url), request_body: requestBody, response_body: text.slice(0, MAX_BODY), truncated: text.length > MAX_BODY });
              }
            }).catch(() => {});
          } catch { /* 读响应失败忽略 */ }
          return resp;
        });
      } catch {
        return origFetch.apply(this, arguments);
      }
    };
  }

  // ── XMLHttpRequest 包装 ──
  const OrigOpen = XMLHttpRequest.prototype.open;
  const OrigSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__jc = { method: String(method || "GET").toUpperCase(), url: String(url || ""), params: paramDict(url) };
    return OrigOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    const meta = this.__jc;
    if (meta) {
      let requestBody = "";
      if (typeof body === "string" && body.length < 4096) requestBody = body;
      this.addEventListener("load", () => {
        try {
          if (this.responseType === "arraybuffer") {
            // 加密站常用原始字节做 AES（网易实盘：fetch/XHR 文本捕获为零的嫌疑传输）：
            // 解码后按 JSON 形状记录（多为密文，作传输指纹；明文由 JSON.parse/
            // Response.json 钩子另行捕获）
            const text = new TextDecoder("utf-8", { fatal: false }).decode(this.response);
            if (looksJson(text)) {
              push({ ...meta, request_body: requestBody, response_body: text.slice(0, MAX_BODY), truncated: text.length > MAX_BODY });
            }
            return;
          }
          if (this.responseType && this.responseType !== "text" && this.responseType !== "json") return;
          const text = this.responseType === "json" ? JSON.stringify(this.response) : this.responseText;
          if (looksJson(text)) push({ ...meta, request_body: requestBody, response_body: String(text).slice(0, MAX_BODY), truncated: String(text).length > MAX_BODY });
        } catch { /* 忽略 */ }
      });
    }
    return OrigSend.apply(this, arguments);
  };
})();

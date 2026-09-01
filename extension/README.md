# JobCheck 浏览器插件（M2）

## 安装（开发者模式）

1. 打开 Chrome/Edge 的 `chrome://extensions/`
2. 右上角打开「开发者模式」
3. 点「加载已解压的扩展程序」，选择本目录（`extension/`）

## 工作原理

- 内容脚本只注入平台自身页面（默认 `http://localhost:5173`，上线后需在 `manifest.json` 的 `content_scripts.matches` 里加上正式域名）
- 平台页点击「去登录」→ 扩展新开标签页打开招聘官网登录页 → 你正常登录（短信验证码到自己手机）
- 扩展监听 Cookie 变化：门户配置的会话 Cookie 齐备后，自动收集该域 Cookie，凭一次性 `bind_token` 回传平台完成激活，然后自动关闭登录页
- **只读**：插件不注入招聘网站页面、不执行任何写操作，仅在平台页与扩展之间中继消息

## 隐私

Cookie 经 HTTPS 直接送往你自己的平台实例并 AES-256-GCM 加密存储，不经过任何第三方。

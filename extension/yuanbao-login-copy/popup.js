// Reads the cookies the browser would send to yuanbao.tencent.com and copies them to the clipboard.
// Nothing is sent over the network and nothing is stored by the extension.
const URL = "https://yuanbao.tencent.com/";

document.getElementById("copy").addEventListener("click", async () => {
  const msg = document.getElementById("msg");
  try {
    // chrome.cookies.getAll({url}) 返回浏览器会随该地址请求发送的 Cookie，
    // 包括父域 .tencent.com 下的 Cookie，并按 Chrome 规则过滤路径与 Secure。
    const all = await chrome.cookies.getAll({ url: URL });
    const now = Date.now() / 1000;
    const found = new Map();
    for (const c of all) {
      if (c.expirationDate && c.expirationDate <= now) continue;
      if (!c.name || /[\r\n]/.test(c.value)) continue;
      found.set(c.name, c);
    }
    if (found.size === 0) {
      // 诊断信息：只显示数量与 Cookie 名，绝不显示值
      const names = all.map((c) => `${c.name}@${c.domain}`);
      msg.textContent = `没有找到未过期的元宝登录态。可见 ${all.length} 项${names.length ? "（" + names.join(", ") + "）" : ""}`;
      return;
    }
    const pairs = [...found.values()].map((c) => `${c.name}=${c.value}`);
    await navigator.clipboard.writeText(pairs.join("; "));
    msg.textContent = `已复制 ${pairs.length} 项。回到拾影，粘贴到 Cookie 输入框并保存。`;
  } catch (err) {
    msg.textContent = "复制失败，请重试。";
  }
});

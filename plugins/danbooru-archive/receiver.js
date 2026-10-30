// Only the host's configured loopback OneBot event stream is read here.
// No OneBot action is sent on this socket and no credentials enter the status page.
export function isLoopbackWebSocket(value) {
  try {
    const u = new URL(value);
    return ['ws:', 'wss:'].includes(u.protocol) && ['127.0.0.1', 'localhost', '[::1]'].includes(u.hostname) && !u.username && !u.password;
  } catch { return false; }
}

async function hostDependencies() {
  const [ws, config, community] = await Promise.all([
    import('ws'), import('../../src/config.js'), import('../../src/community.js')
  ]);
  return { WebSocket: ws.default, getConfig: config.getConfig, isGloballyBlocked: community.isGloballyBlocked };
}

export function createReceiver({ onEvent, onState, getScope }, options = {}) {
  let active = false, loading = false, dependencies = null, socket = null;
  let interval = null, retryTimer = null, failures = 0, connectionKey = '', epoch = 0;
  const loadDependencies = options.loadDependencies || hostDependencies;
  const pollMs = options.pollMs || 15000;
  const retryBaseMs = options.retryBaseMs || 3000;
  function state(status, message) { onState?.({ status, message }); }
  function closeSocket() {
    const old = socket; socket = null;
    if (old) { try { old.terminate(); } catch { try { old.close(); } catch { /* closed */ } } }
  }
  function schedule() {
    if (!active || retryTimer || getScope() !== 'all_groups') return;
    const delay = Math.min(60000, retryBaseMs * 2 ** Math.min(failures, 5));
    retryTimer = setTimeout(() => { retryTimer = null; connect(); }, delay);
    retryTimer.unref?.();
  }
  function connection() {
    let cfg = {};
    try { cfg = dependencies.getConfig() || {}; } catch { /* fail closed: empty URL */ }
    const url = String(cfg.snowluma?.wsUrl || '');
    const token = String(cfg.snowluma?.accessToken || '');
    return { cfg, url, token, key: url + '\n' + token };
  }
  function connect() {
    if (!active || !dependencies || socket || getScope() !== 'all_groups') return;
    const { url, token, key } = connection();
    connectionKey = key;
    if (!isLoopbackWebSocket(url)) { state('error', '所有群收图仅支持 QQ Agent 已配置的本机 OneBot 地址'); return; }
    state('connecting', '正在连接所有群图片接收通道');
    let ws;
    try {
      // Keep the configured URL unmodified; credentials travel only in the header.
      ws = new dependencies.WebSocket(url, { headers: token ? { authorization: `Bearer ${token}` } : {}, maxPayload: 40 * 1024 * 1024, handshakeTimeout: 10000 });
    } catch { failures++; state('error', '群图片接收通道暂不可用，稍后自动重连'); schedule(); return; }
    socket = ws;
    ws.on('open', () => { if (socket !== ws || !active) return; failures = 0; state('connected', '已连接：自动接收所有群的图片（保留屏蔽规则）'); });
    ws.on('error', () => { if (socket === ws && active) state('error', '群图片接收通道连接失败，稍后自动重连'); });
    ws.on('close', () => {
      if (socket !== ws) return;
      socket = null;
      if (active) { failures++; state('reconnecting', '群图片接收通道断开，正在自动重连'); schedule(); }
    });
    ws.on('message', (buffer) => {
      if (socket !== ws || !active) return;
      let event;
      try { event = JSON.parse(buffer.toString('utf8')); } catch { return; }
      if (!event || typeof event !== 'object' || Array.isArray(event)) return;
      if (event.post_type !== 'message' || event.message_type !== 'group') return;
      const groupId = String(event.group_id ?? '');
      const senderId = String(event.user_id ?? '');
      if (!/^\d+$/.test(groupId) || !/^\d+$/.test(senderId) || senderId === String(event.self_id)) return;
      try {
        const cfg = dependencies.getConfig() || {};
        const denied = cfg.deny?.group ?? cfg.deny?.groups ?? [];
        if (Array.isArray(denied) && denied.map(String).includes(groupId)) return;
        if (dependencies.isGloballyBlocked(senderId)) return;
        const groupBlocked = cfg.blocklist?.[groupId];
        if (Array.isArray(groupBlocked) && groupBlocked.map(String).includes(senderId)) return;
        onEvent(event);
      } catch { state('error', '图片消息或屏蔽配置读取失败，已跳过本条消息'); }
    });
  }
  async function tick() {
    if (!active) return;
    if (getScope() !== 'all_groups') {
      clearTimeout(retryTimer); retryTimer = null; closeSocket();
      state('host_only', '仅处理 QQ Agent 聊天白名单内的群'); return;
    }
    if (!dependencies) {
      if (loading) return;
      loading = true; const ownEpoch = epoch;
      try { const loaded = await loadDependencies(); if (!active || epoch !== ownEpoch) return; dependencies = loaded; }
      catch { if (active) state('error', '无法初始化所有群收图通道，请查看插件依赖'); }
      finally { loading = false; }
      if (!active || !dependencies) return;
    }
    const { key } = connection();
    if (connectionKey && key !== connectionKey) { clearTimeout(retryTimer); retryTimer = null; closeSocket(); failures = 0; }
    if (!socket && !retryTimer) connect();
  }
  return {
    start() {
      if (active) return;
      active = true; epoch++; tick();
      interval = setInterval(tick, pollMs); interval.unref?.();
    },
    stop() {
      active = false; epoch++;
      clearInterval(interval); interval = null;
      clearTimeout(retryTimer); retryTimer = null;
      closeSocket(); state('disabled', '群图片接收通道已关闭');
    }
  };
}

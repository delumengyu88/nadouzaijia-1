const app = document.querySelector("#app");
const state = {
  me: null,
  overdue: 0,
  stale: false,
  evidence: [],
  evidenceId: null,
  detail: null,
  invites: [],
  stats: [],
  user: null,
  locks: [],
  staff: [],
  orders: [],
  orderId: null,
  splash: null,
  notes: [],
  examples: [],
  programs: [],
  logs: [],
  index: null,
  modal: null,
  message: "",
  userQuery: "",
};
let generation = 0;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function explain(data, status) {
  if (status === 423) return "尝试过多，请 30 分钟后再试或联系管理员。";
  if (status === 401) return "请先登录。";
  if (status === 403) return "当前账号不能做这项操作。";
  if (status === 404) return "没有找到对应记录。";
  if (status === 409) return "当前状态不能进行这项操作。";
  return "操作没有完成，请检查填写内容。";
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = sessionStorage.getItem("nadou-token");
  if (token) headers.set("Authorization", "Bearer " + token);
  let body;
  if (options.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.json);
  }
  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body,
    credentials: "same-origin",
  });
  const type = response.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await response.json() : await response.text();
  return { ok: response.ok, status: response.status, data };
}

function clockParts(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const pick = (type) => {
    const found = parts.find((part) => part.type === type);
    return found ? found.value : "";
  };
  const hour = pick("hour") === "24" ? "00" : pick("hour");
  return { year: pick("year"), month: pick("month"), day: pick("day"), hour, minute: pick("minute") };
}

function fmt(value) {
  const parts = clockParts(value);
  if (!parts) return "—";
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

function ymd(value) {
  if (!value) return "—";
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  const parts = clockParts(text);
  return parts ? `${parts.year}-${parts.month}-${parts.day}` : "—";
}

function shortWhen(value) {
  const text = fmt(value);
  return text === "—" ? text : text.slice(5);
}

function money(amount) {
  if (amount == null) return "—";
  return `¥${amount}`;
}

function roleName(role) {
  return role === "admin" ? "管理员" : "客服";
}

function tierName(kind) {
  if (kind === "vip") return "VIP";
  if (kind === "regular") return "普通会员";
  if (kind === "program_30") return "单节目 30 天";
  if (kind === "program_90") return "单节目 90 天";
  if (kind === "screenshot") return "单张截图";
  return "权益";
}

function platformHtml(value) {
  if (!value || value === "平台未识别") {
    return '<span class="tag tag-gray unresolved">平台未识别 ?</span>';
  }
  const parts = String(value).split(" · ");
  if (parts.length < 2) return escapeHtml(value);
  return `${escapeHtml(parts[0])} <span class="muted">${escapeHtml(parts.slice(1).join(" · "))}</span>`;
}

function icon(name) {
  const paths = {
    evidence:
      '<path d="M4 2.5h5.5L13 6v7.5H4V2.5z" stroke="currentColor" stroke-width="1.3"/><path d="M9.5 2.5V6H13" stroke="currentColor" stroke-width="1.3"/>',
    chat: '<path d="M3.2 3.5h9.6c.7 0 1.2.5 1.2 1.2v5.6c0 .7-.5 1.2-1.2 1.2H7.1L4 13.7V11.5H3.2c-.7 0-1.2-.5-1.2-1.2V4.7c0-.7.5-1.2 1.2-1.2z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
    code: '<rect x="2.5" y="4" width="11" height="8.5" rx="1.2" stroke="currentColor" stroke-width="1.3"/><path d="M5 8.2h6M5 10.2h3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    user: '<circle cx="8" cy="5.5" r="2.2" stroke="currentColor" stroke-width="1.3"/><path d="M3.5 13c.7-2.2 2.3-3.3 4.5-3.3s3.8 1.1 4.5 3.3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    order:
      '<rect x="3" y="3" width="10" height="10" rx="1.2" stroke="currentColor" stroke-width="1.3"/><path d="M5.5 6.5h5M5.5 9.5h3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    content:
      '<rect x="3" y="2.5" width="10" height="11" rx="1.2" stroke="currentColor" stroke-width="1.3"/><path d="M5.5 5.5h5M5.5 8h5M5.5 10.5h3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    log: '<path d="M3.5 3.5h9v9h-9v-9z" stroke="currentColor" stroke-width="1.3"/><path d="M5.5 6.5h5M5.5 9h3.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>',
    index:
      '<path d="M3 11.5 6.2 8l2.1 2.1L13 5.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/><path d="M10.5 5.5H13V8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>',
  };
  return `<svg width="16" height="16" viewBox="0 0 16 16" fill="none">${paths[name] || ""}</svg>`;
}

function shell(title, body, screen) {
  document.body.classList.toggle("screen", Boolean(screen));
  const admin = state.me && state.me.role === "admin";
  const items = [
    ["#/w02", "取证申请", "evidence", true],
    ["https://mpkf.weixin.qq.com/", "客服消息", "chat", true],
    ["#/w03", "邀请码", "code", admin],
    ["#/w04", "用户与会员", "user", true],
    ["#/w05", "订单", "order", true],
    ["#/w06", "内容配置", "content", admin],
    ["#/w08", "操作日志", "log", admin],
    ["#/w09", "数据与索引", "index", admin],
  ];
  const route = location.hash || "#/w02";
  const nav = items
    .filter((item) => item[3])
    .map((item) => {
      const external = item[0].startsWith("http");
      const active = !external && route === item[0] ? "active" : "";
      const extra = external ? ' target="_blank" rel="noopener noreferrer"' : "";
      const mark = external ? '<span class="ext">↗</span>' : "";
      return `<a class="${active}" href="${item[0]}"${extra}>${icon(item[2])}${item[1]}${mark}</a>`;
    })
    .join("");
  const alerts = [
    state.overdue
      ? `<span class="top-alert">取证超时未受理 ${state.overdue} 单</span>`
      : "",
    admin && state.stale ? '<span class="top-alert warn">索引已断更超过 2 天</span>' : "",
  ].join("");
  const initial = escapeHtml((state.me.username || "客").slice(0, 1));
  app.innerHTML = `
    <aside class="sidebar">
      <div class="logo">那豆在架 工作台</div>
      <nav class="nav"><div class="nav-label">工作台</div>${nav}</nav>
    </aside>
    <div class="main">
      <header class="topbar">
        <h1>${escapeHtml(title)}</h1>
        <div class="topbar-right">
          ${alerts}
          <div class="user-chip">
            <div class="avatar">${initial}</div>
            <span>${escapeHtml(state.me.username)} <span class="role">· ${roleName(state.me.role)}</span></span>
            <button class="btn btn-default btn-sm" type="button" data-act="logout">退出</button>
          </div>
        </div>
      </header>
      <div class="content${screen ? " workspace" : ""}">
        ${state.message ? `<div class="banner warn">${escapeHtml(state.message)}</div>` : ""}
        ${body}
      </div>
    </div>
    ${state.modal || ""}`;
}

function renderLogin(message) {
  document.body.classList.remove("screen");
  document.title = "登录 · 那豆在架 工作台";
  app.innerHTML = `
    <div class="login-page">
      <div class="login-left">
        <div class="brand">那豆在架 工作台</div>
      </div>
      <div class="login-right">
        <form class="login-card" data-act="login">
          <h2>账号登录</h2>
          <div class="err${message ? " show" : ""}">${escapeHtml(message || "")}</div>
          <div class="field"><label>账号</label><input class="input" name="username" autocomplete="username"></div>
          <div class="field"><label>密码</label><input class="input" name="password" type="password" autocomplete="current-password"></div>
          <button class="btn btn-primary" type="submit">登录</button>
        </form>
      </div>
    </div>`;
}

async function refreshAlerts() {
  const evidence = await api("/api/v1/admin/evidence-requests");
  state.overdue = evidence.ok ? evidence.data.filter((row) => row.overdue).length : 0;
  if (state.me.role === "admin") {
    const index = await api("/api/v1/admin/index/status");
    state.stale = Boolean(index.ok && index.data.stale);
    if (index.ok) state.index = index.data;
  } else {
    state.stale = false;
  }
}

function denied() {
  shell("无法查看", '<div class="card"><div class="card-bd">当前账号不能查看此页。</div></div>', false);
}

function shanghaiDay(value) {
  const text = ymd(value);
  return text === "—" ? "" : text;
}

function dayText(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value);
}

function memberTag(row) {
  if (row.membership === "vip" || row.vip) return '<span class="tag tag-money">VIP</span>';
  if (row.membership === "regular") return '<span class="tag tag-blue">普通会员</span>';
  return '<span class="tag tag-gray">免费</span>';
}

function maskPhone(phone) {
  const text = String(phone || "");
  if (text.length <= 4) return text;
  return `${"*".repeat(text.length - 4)}${text.slice(-4)}`;
}

function evidenceLabel(row) {
  if (row.overdue) return '<span class="tag tag-red tag-dot">已提交 · 超时未受理</span>';
  const tone = {
    已提交: "tag-orange",
    已受理: "tag-blue",
    "待付款（线下）": "tag-orange",
    取证中: "tag-blue",
    已交付: "tag-green",
    已完成: "tag-green",
    取证失败: "tag-red",
    已关闭: "tag-gray",
    已取消: "tag-gray",
  };
  return `<span class="tag ${tone[row.status] || "tag-gray"} tag-dot">${escapeHtml(row.status)}</span>`;
}

async function renderEvidence() {
  const listed = await api("/api/v1/admin/evidence-requests");
  if (!listed.ok) {
    state.message = explain(listed.data, listed.status);
    shell("取证申请", "", true);
    return;
  }
  state.evidence = listed.data;
  if (state.evidenceId) {
    const detail = await api(`/api/v1/admin/evidence-requests/${state.evidenceId}`);
    state.detail = detail.ok ? detail.data : null;
  }
  const statusFilter = state.evidenceStatus || "全部";
  const typeFilter = state.evidenceType || "全部";
  const evidenceQuery = (state.evidenceQuery || "").trim();
  const fromDay = state.evidenceFrom || "";
  const toDay = state.evidenceTo || "";
  const vipOnly = Boolean(state.evidenceVip);
  const visible = state.evidence.filter((row) => {
    if (statusFilter !== "全部" && row.status !== statusFilter) return false;
    if (typeFilter !== "全部" && row.request_type !== typeFilter) return false;
    if (vipOnly && !row.vip) return false;
    const day = shanghaiDay(row.created_at);
    if (fromDay && day < fromDay) return false;
    if (toDay && day > toDay) return false;
    if (!evidenceQuery) return true;
    return `${row.application_number} ${row.program_name || ""} ${row.phone || ""}`.includes(evidenceQuery);
  });
  const rows = visible
    .map((row) => {
      const selected = row.id === state.evidenceId ? "selected" : "";
      const late = row.overdue ? "overdue" : "";
      const pin = row.overdue ? '<span class="pin">置顶</span> ' : "";
      const who = `<div>${escapeHtml(row.contact_name || "—")}</div><div class="muted">${escapeHtml(row.phone || "—")}</div>`;
      const program = row.program_name || "—";
      const quick =
        row.status === "已提交"
          ? `<button class="btn btn-primary btn-sm" type="button" data-act="accept" data-id="${escapeHtml(row.id)}">接单</button>`
          : row.status === "待付款（线下）"
            ? `<button class="btn btn-primary btn-sm" type="button" data-act="pay" data-id="${escapeHtml(row.id)}" data-amount="${escapeHtml(row.quote_amount || "")}">确认收款</button>`
            : "";
      return `<tr class="${selected} ${late}">
        <td class="cell-no">${pin}<span class="mono">${escapeHtml(row.application_number)}</span></td>
        <td class="cell-program" title="${escapeHtml(program)}">${escapeHtml(program)}</td>
        <td>${memberTag(row)}</td>
        <td class="cell-who">${who}</td>
        <td class="cell-status">${evidenceLabel(row)}</td>
        <td class="nowrap">${escapeHtml(shortWhen(row.created_at))}</td>
        <td class="col-actions">${quick}<button class="btn btn-default btn-sm" type="button" data-act="open-evidence" data-id="${escapeHtml(row.id)}">查看</button></td>
      </tr>`;
    })
    .join("");
  const overdue = state.evidence.filter((row) => row.overdue).length;
  const pending = state.evidence.filter((row) => row.status === "已提交").length;
  const working = state.evidence.filter((row) => row.status === "取证中").length;
  const today = shanghaiDay(new Date().toISOString());
  const delivered = state.evidence.filter((row) => shanghaiDay(row.delivered_at) === today).length;
  const vip = state.evidence.filter((row) => row.vip && !["已交付", "已完成", "已关闭", "已取消", "取证失败"].includes(row.status)).length;
  const drawer = state.evidenceId && state.detail ? evidenceDrawer(state.detail) : "";
  shell(
    "取证申请",
    `<div class="workspace-row"><div class="workspace-col">
      <div class="stats cols-5 compact">
        <div class="stat"><div class="k">超时未受理</div><div class="v danger">${overdue}</div></div>
        <div class="stat"><div class="k">已提交</div><div class="v">${pending}</div></div>
        <div class="stat"><div class="k">取证中</div><div class="v">${working}</div></div>
        <div class="stat"><div class="k">今日已交付</div><div class="v">${delivered}</div></div>
        <div class="stat"><div class="k">VIP 待处理</div><div class="v">${vip}</div></div>
      </div>
      <div class="card grow"><div class="card-hd">申请列表<span class="sub">共 ${visible.length} 条</span></div>
        <div class="filter-bar dense">
          <input class="input query" id="evidence-query" value="${escapeHtml(evidenceQuery)}" placeholder="申请号或节目">
          <select class="select status" id="evidence-status">
            ${["全部", "已提交", "已受理", "待付款（线下）", "取证中", "已交付", "已完成", "取证失败", "已关闭", "已取消"].map((item) => `<option${item === statusFilter ? " selected" : ""}>${item}</option>`).join("")}
          </select>
          <select class="select kind" id="evidence-type">
            ${["全部", "常规", "近期"].map((item) => `<option${item === typeFilter ? " selected" : ""}>${item}</option>`).join("")}
          </select>
          <input class="input w-date" id="evidence-from" inputmode="numeric" placeholder="YYYY-MM-DD" value="${escapeHtml(fromDay)}">
          <input class="input w-date" id="evidence-to" inputmode="numeric" placeholder="YYYY-MM-DD" value="${escapeHtml(toDay)}">
          <label class="help vip-filter" id="evidence-vip-label" style="margin:0"><input id="evidence-vip" type="checkbox"${vipOnly ? " checked" : ""}> VIP 优先</label>
          <button class="btn btn-primary btn-sm" id="evidence-search" type="button" data-act="evidence-filter">查询</button>
        </div>
        <div class="card-bd flush"><table class="data tight"><thead><tr>
          <th class="cell-no">申请号</th><th>节目</th><th>会员</th><th>联系人 / 手机</th><th class="cell-status">状态</th><th>提交时间</th><th class="col-actions"></th>
        </tr></thead><tbody>${rows || '<tr><td colspan="7">暂无申请</td></tr>'}</tbody></table></div></div>
    </div>${drawer}</div>`,
    true,
  );
}

function timelineHtml(row) {
  return (row.timeline || [])
    .map((step) => {
      const klass = step.state === "done" ? "done" : step.state === "wait" ? "wait" : step.label === "取证失败" ? "bad" : "";
      const when = step.at ? fmt(step.at) : step.state === "wait" ? "待进行" : "";
      return `<div class="tl-item ${klass}"><strong>${escapeHtml(step.label)}</strong><span class="t">${escapeHtml(when)}</span></div>`;
    })
    .join("");
}

function evidenceDrawer(row) {
  const notes = (row.notes || [])
    .map(
      (note) =>
        `<div class="note-item"><div class="meta">${escapeHtml(fmt(note.created_at))} · ${escapeHtml(note.author)}</div><div class="body">${escapeHtml(note.text)}</div></div>`,
    )
    .join("");
  let actions = "";
  if (row.status === "已提交") {
    actions = `<button class="btn btn-primary" type="button" data-act="accept" data-id="${escapeHtml(row.id)}">接单</button>
      <button class="btn btn-default" type="button" data-act="cancel" data-id="${escapeHtml(row.id)}">取消申请</button>`;
  } else if (row.status === "已受理") {
    actions = `
      <input class="input w-xs" id="quote-amount" inputmode="numeric" value="${escapeHtml(row.quote_amount || "")}" placeholder="金额">
      <button class="btn btn-primary" type="button" data-act="quote" data-id="${escapeHtml(row.id)}">填写报价</button>
      <button class="btn btn-danger" type="button" data-act="fail-open" data-id="${escapeHtml(row.id)}">标记失败</button>`;
  } else if (row.status === "待付款（线下）") {
    actions = `
      <input class="input w-xs" id="quote-amount" inputmode="numeric" value="${escapeHtml(row.quote_amount || "")}" placeholder="金额">
      <button class="btn btn-primary" type="button" data-act="pay" data-id="${escapeHtml(row.id)}">确认收款</button>
      <button class="btn btn-danger" type="button" data-act="fail-open" data-id="${escapeHtml(row.id)}">标记失败</button>`;
  } else if (row.status === "取证中") {
    actions = `
      <button class="btn btn-primary" type="button" data-act="deliver" data-id="${escapeHtml(row.id)}">标记已交付</button>
      <button class="btn btn-danger" type="button" data-act="fail-open" data-id="${escapeHtml(row.id)}">标记失败</button>`;
  } else if (row.status === "已交付") {
    actions = `<button class="btn btn-primary" type="button" data-act="complete" data-id="${escapeHtml(row.id)}">确认完成</button>`;
  } else if (row.status === "取证失败") {
    actions = `<button class="btn btn-default" type="button" data-act="close" data-id="${escapeHtml(row.id)}">关闭</button>`;
  }
  const deliver =
    row.status === "取证中"
      ? `<div class="deliver-panel"><div class="section-title">记录线下交付</div>
          <div class="field"><label>交付方式</label>
            <select class="select" id="deliver-method"><option>当面</option><option>快递</option><option>企业网盘</option><option>其他</option></select>
          </div>
          <div class="field" style="margin-top:8px"><label>说明</label><input class="input" id="deliver-note" placeholder="选「其他」时必填"></div>
          <p class="help">交付时间记为提交时刻，经办人是当前登录账号。不提供下载链接。</p>
        </div>`
      : "";
  return `<aside class="drawer">
    <div class="drawer-hd">${escapeHtml(row.application_number)}<span class="sub">${escapeHtml(row.status)}</span><button class="btn btn-default btn-sm" type="button" data-act="close-drawer">关闭</button></div>
    <p class="cs-hint"><a href="https://mpkf.weixin.qq.com/" target="_blank" rel="noopener noreferrer">回复请到微信小程序客服 ↗</a></p>
    <div class="drawer-bd"><div class="drawer-scroll">
      <div class="kv">
        <div class="k">类型</div><div class="v">${escapeHtml(row.request_type)}</div>
        <div class="k">节目</div><div class="v">${escapeHtml(row.program_name || "—")}</div>
        <div class="k">平台</div><div class="v">${platformHtml(row.platform_label)}</div>
        <div class="k">会员</div><div class="v">${memberTag(row)}</div>
        <div class="k">联系人</div><div class="v">${escapeHtml(row.contact_name || "—")} ${escapeHtml(row.phone || "")}</div>
        <div class="k">单位</div><div class="v">${escapeHtml(row.organization || "—")}</div>
        <div class="k">报价</div><div class="v money">${money(row.quote_amount)}</div>
        <div class="k">收款</div><div class="v money">${money(row.payment_amount)}</div>
        <div class="k">说明</div><div class="v">${escapeHtml(row.requirement || "—")}</div>
      </div>
      <div class="hr"></div>
      <div class="section-title">状态时间线</div>
      <div class="timeline">${timelineHtml(row)}</div>
      <div class="hr"></div>
      <div class="section-title">沟通备注</div>
      <div class="note-list">${notes || '<div class="muted">暂无备注</div>'}</div>
      <div class="note-add"><input class="input" id="note-text" placeholder="记录沟通要点"><button class="btn btn-default btn-sm" type="button" data-act="note" data-id="${escapeHtml(row.id)}">添加备注</button></div>
      ${deliver}
    </div></div>
    <div class="drawer-ft">${actions}</div>
  </aside>`;
}

function percent(part, whole) {
  if (!whole) return "0%";
  return `${Math.round((part / whole) * 100)}%`;
}

async function renderInvites() {
  if (state.me.role !== "admin") return denied();
  const [stats, rows] = await Promise.all([
    api("/api/v1/admin/invite-codes/stats"),
    api("/api/v1/admin/invite-codes"),
  ]);
  if (!stats.ok || !rows.ok) {
    state.message = explain(null, stats.status || rows.status);
    shell("邀请码", "", false);
    return;
  }
  state.stats = stats.data;
  state.invites = rows.data;
  const statRows = state.stats
    .map((row) => {
      const paid = `${row.paid_within_30_days} / ${row.redeemed}（${percent(row.paid_within_30_days, row.redeemed)}）`;
      return `<tr><td>${escapeHtml(row.channel)}</td><td>${row.generated}</td><td>${row.redeemed}</td><td>${row.unused}</td><td>${row.expired}</td><td>${row.voided}</td><td>${percent(row.redeemed, row.generated)}</td><td>${paid}</td></tr>`;
    })
    .join("");
  const tone = { 已兑换: "tag-green", 未使用: "tag-blue", 已过期: "tag-gray", 已作废: "tag-red" };
  const list = state.invites
    .map((row) => {
      const action =
        row.status === "未使用"
          ? `<button class="link danger" type="button" data-act="void-open" data-id="${escapeHtml(row.id)}" data-code="${escapeHtml(row.code)}">作废</button>`
          : '<span class="muted">—</span>';
      return `<tr>
        <td class="mono">${escapeHtml(row.code)}</td>
        <td>${escapeHtml(row.channel)}</td>
        <td>${escapeHtml(row.creator || "—")}</td>
        <td><span class="tag ${tone[row.status] || "tag-gray"} tag-dot">${escapeHtml(row.status)}</span></td>
        <td class="nowrap">${escapeHtml(fmt(row.created_at))}</td>
        <td class="nowrap">${escapeHtml(ymd(row.expires_at))}</td>
        <td>${escapeHtml(row.redeemed_phone || "—")}</td>
        <td class="nowrap">${escapeHtml(row.redeemed_at ? fmt(row.redeemed_at) : "—")}</td>
        <td>${action}</td>
      </tr>`;
    })
    .join("");
  shell(
    "邀请码",
    `<div class="card"><div class="card-hd">渠道转化统计</div><div class="card-bd flush"><table class="data"><thead><tr>
      <th>渠道</th><th>生成数</th><th>已兑换</th><th>未使用</th><th>已过期</th><th>已作废</th><th>兑换率</th><th>兑换后 30 天内付费转化</th>
    </tr></thead><tbody>${statRows || '<tr><td colspan="8">暂无渠道</td></tr>'}</tbody></table></div></div>
    <div><div class="card" style="margin:0"><div class="card-hd">批量生成</div><div class="card-bd">
      <div class="filters">
        <div class="field"><label>数量<span class="req">*</span></label><input class="input w-xs" id="invite-count" value="10"></div>
        <div class="field"><label>渠道标签<span class="req">*</span></label><input class="input w-md" id="invite-channel" value="临江示例律所"></div>
        <div class="field"><label>码有效期<span class="req">*</span></label><input class="input w-md w-date" id="invite-until" inputmode="numeric" placeholder="YYYY-MM-DD"></div>
      </div>
      <button class="btn btn-primary" type="button" data-act="invite-create" style="margin-top:12px">生成邀请码</button>
    </div></div>
    </div>
    <div class="card" style="margin-top:16px"><div class="card-hd">邀请码列表
      <button class="btn btn-default btn-sm" type="button" data-act="invite-export">导出</button>
    </div><div class="card-bd flush"><table class="data"><thead><tr>
      <th>邀请码</th><th>渠道</th><th>生成人</th><th>状态</th><th>生成时间</th><th>有效期至</th><th>兑换人</th><th>兑换时间</th><th>操作</th>
    </tr></thead><tbody>${list || '<tr><td colspan="9">暂无邀请码</td></tr>'}</tbody></table></div></div>`,
    false,
  );
  const until = document.querySelector("#invite-until");
  if (until && !until.value) {
    until.value = shanghaiDay(new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString());
  }
}

async function renderUsers() {
  const admin = state.me.role === "admin";
  if (admin && state.locks.length === 0) {
    const locks = await api("/api/v1/admin/staff/locks");
    state.locks = locks.ok ? locks.data : [];
  }
  const user = state.user;
  const activeGrants = user ? (user.entitlements || []).filter((row) => row.active) : [];
  const vipGrant = activeGrants.find((row) => row.kind === "vip");
  const inviteGrant = activeGrants.find((row) => row.kind === "regular" && row.source === "invite");
  let tierLine = "无会员";
  if (user && vipGrant && inviteGrant) {
    tierLine = `普通会员 至 ${ymd(inviteGrant.expires_at)}`;
  } else if (user && vipGrant) {
    tierLine = `VIP 至 ${ymd(vipGrant.expires_at)}`;
  } else if (user && inviteGrant) {
    tierLine = `普通会员 至 ${ymd(inviteGrant.expires_at)}`;
  }
  const grants = user
    ? (user.entitlements || [])
        .map((row) => {
          const label = row.source_label ? ` <span class="tag tag-gray">${escapeHtml(row.source_label)}</span>` : "";
          return `<tr><td>${tierName(row.kind)}</td><td>${escapeHtml(row.program_name || "—")}</td><td>${escapeHtml(ymd(row.expires_at))}</td><td>${row.active ? "有效" : "已结束"}${label}</td></tr>`;
        })
        .join("")
    : "";
  const invites = user && user.invites && user.invites.length
    ? user.invites.map((row) => `${escapeHtml(row.code)} · ${escapeHtml(row.channel)} · ${escapeHtml(fmt(row.redeemed_at))}`).join("；")
    : "未兑换";
  const profile = user
    ? `<div class="card" style="margin:0"><div class="card-hd">用户概览 ${user.ban_type ? '<span class="tag tag-red">已封禁</span>' : '<span class="tag tag-green">正常</span>'}</div>
      <div class="card-bd"><div class="kv">
        <div class="k">当前档</div><div class="v">${escapeHtml(tierLine)}</div>
        <div class="k">姓名</div><div class="v">${escapeHtml(user.display_name || "—")}</div>
        <div class="k">账号标识</div><div class="v mono">${escapeHtml(user.wechat_openid || "—")}</div>
        <div class="k">手机号</div><div class="v">${escapeHtml(user.phone || "—")}</div>
        <div class="k">邀请码</div><div class="v">${invites}</div>
        <div class="k">封禁</div><div class="v">${escapeHtml(user.ban_type ? `${user.ban_type === "permanent" ? "永久" : "临时"} · ${user.ban_reason || ""}` : "无")}</div>
      </div><div class="hr"></div><div class="section-title">权益与解锁记录</div>
      <table class="data"><thead><tr><th>类型</th><th>对象</th><th>到期时间</th><th>状态</th></tr></thead>
      <tbody>${grants || '<tr><td colspan="4">暂无权益</td></tr>'}</tbody></table>
      ${user.ban_type && admin ? `<div style="margin-top:12px"><button class="btn btn-primary" type="button" data-act="unban" data-id="${escapeHtml(user.id)}">解禁</button></div>` : ""}
      </div></div>`
    : '<div class="card"><div class="card-bd">输入手机号后查询。</div></div>';
  const grantCard = admin && user
    ? `<div class="card" style="margin:0 0 16px"><div class="card-hd">手动开通 / 延长会员</div><div class="card-bd">
        <div class="field"><label>档位</label><select class="select" id="grant-tier" style="width:100%"><option value="vip">VIP</option><option value="regular">普通会员</option></select></div>
        <div class="field" style="margin-top:10px"><label>天数</label><input class="input" id="grant-days" value="30"></div>
        <div class="field" style="margin-top:10px"><label>原因</label><textarea class="textarea" id="grant-reason" rows="3" placeholder="必填"></textarea></div>
        <p class="help">必须填写原因。</p>
        <button class="btn btn-primary" type="button" data-act="grant">确认开通 / 延长</button>
      </div></div>
      <div class="card" style="margin:0 0 16px"><div class="card-hd">封禁小程序账号</div><div class="card-bd">
        <p class="help" style="margin-top:0">结束时间仅临时封禁时填写。</p>
        <button class="btn btn-danger" type="button" data-act="ban-open">封禁此账号</button>
      </div></div>`
    : "";
  const lockRows = (state.locks || [])
    .map(
      (row) => `<tr><td class="mono">${escapeHtml(row.username)}</td><td>${roleName(row.role)}</td><td class="nowrap">${escapeHtml(fmt(row.locked_until))}</td><td><button class="btn btn-primary btn-sm" type="button" data-act="unlock" data-id="${escapeHtml(row.account_id)}">解锁登录</button></td></tr>`,
    )
    .join("");
  const locks = admin
    ? `<div class="card" style="margin:0"><div class="card-hd">工作台登录锁定</div><div class="card-bd flush">
        <table class="data"><thead><tr><th>账号</th><th>角色</th><th>可自动解锁</th><th></th></tr></thead>
        <tbody>${lockRows || '<tr><td colspan="4">当前没有锁定账号</td></tr>'}</tbody></table></div>
        <div class="card-bd"><div class="filters">
          <input class="input w-sm" id="staff-name" placeholder="新账号">
          <input class="input w-sm" id="staff-pass" type="password" placeholder="密码">
          <select class="select w-sm" id="staff-role"><option value="support">客服</option><option value="admin">管理员</option></select>
          <button class="btn btn-default" type="button" data-act="staff-create">新建账号</button>
        </div></div></div>`
    : "";
  shell(
    "用户与会员",
    `${admin ? "" : '<p class="help">当前为客服视角：只读。</p>'}
    <div class="card"><div class="card-hd">查找用户</div><div class="card-bd"><div class="filters">
      <div class="field"><label>手机号</label><input class="input w-xl" id="user-query" value="${escapeHtml(admin ? state.userQuery || "" : state.userQuery ? maskPhone(state.userQuery) : "")}" placeholder="输入手机号"></div>
      <button class="btn btn-primary" type="button" data-act="user-search">查询</button>
    </div></div></div>
    <div class="grid-2-1">${profile}<div>${grantCard}${locks}</div></div>`,
    false,
  );
}

function orderLabel(status) {
  const tone = { 发放异常: "tag-red", 已发放: "tag-green", 已支付: "tag-blue", 待支付: "tag-orange", 已退款: "tag-gray" };
  return `<span class="tag ${tone[status] || "tag-gray"} tag-dot">${escapeHtml(status)}</span>`;
}

async function renderOrders() {
  const listed = await api("/api/v1/admin/orders");
  if (!listed.ok) {
    state.message = explain(listed.data, listed.status);
    shell("订单", "", true);
    return;
  }
  state.orders = listed.data;
  if (!state.orderId || !state.orders.some((row) => row.id === state.orderId)) {
    const first = state.orders.find((row) => row.status === "发放异常") || state.orders[0];
    state.orderId = first ? first.id : null;
  }
  const filter = state.orderFilter || "全部";
  const query = (state.orderQuery || "").trim();
  const shown = state.orders.filter((row) => {
    if (filter !== "全部" && row.status !== filter) return false;
    if (!query) return true;
    return `${row.order_number} ${row.phone || ""} ${row.user_id}`.includes(query);
  });
  const rows = shown
    .map((row) => {
      const selected = row.id === state.orderId ? "selected" : "";
      const late = row.status === "发放异常" ? "overdue" : "";
      const button =
        row.status === "发放异常"
          ? `<button class="btn btn-primary btn-sm" type="button" data-act="reissue-open" data-id="${escapeHtml(row.id)}">补发</button>`
          : `<button class="btn btn-default btn-sm" type="button" data-act="open-order" data-id="${escapeHtml(row.id)}">查看</button>`;
      return `<tr class="${selected} ${late}" data-act="open-order" data-id="${escapeHtml(row.id)}">
        <td class="mono nowrap">${escapeHtml(row.order_number)}</td>
        <td>${escapeHtml(row.phone || "—")}</td>
        <td>${tierName(row.tier)} ${row.duration_days} 天</td>
        <td class="money">${money(row.amount)}</td>
        <td>${escapeHtml(row.client || "—")}</td>
        <td class="nowrap">${orderLabel(row.status)}</td>
        <td class="nowrap">${escapeHtml(fmt(row.paid_at || row.created_at))}</td>
        <td>${row.attempts == null ? "—" : `${row.attempts} 次`}</td>
        <td>${button}</td>
      </tr>`;
    })
    .join("");
  const broken = state.orders.filter((row) => row.status === "发放异常").length;
  const waiting = state.orders.filter((row) => row.status === "待支付").length;
  const refunded = state.orders.filter((row) => row.status === "已退款").length;
  const current = state.orders.find((row) => row.id === state.orderId);
  const admin = state.me.role === "admin";
  const drawer = current ? orderDrawer(current, admin) : '<aside class="drawer"><div class="drawer-bd">选择一笔订单。</div></aside>';
  const exportButton = admin ? '<button class="btn btn-default btn-sm" type="button" data-act="order-export">导出</button>' : "";
  shell(
    "订单",
    `<div class="workspace-row"><div class="workspace-col">
      <div class="stats cols-4 compact">
        <div class="stat"><div class="k">发放异常</div><div class="v danger">${broken}</div></div>
        <div class="stat"><div class="k">订单总数</div><div class="v">${state.orders.length}</div></div>
        <div class="stat"><div class="k">待支付</div><div class="v">${waiting}</div></div>
        <div class="stat"><div class="k">已退款</div><div class="v">${refunded}</div></div>
      </div>
      <div class="card grow"><div class="card-hd">订单
        <div style="display:flex;gap:8px;font-weight:400">
          <input class="input w-md" id="order-query" value="${escapeHtml(query)}" placeholder="订单号或手机">
          <select class="select w-sm" id="order-filter">
            ${["全部", "发放异常", "待支付", "已支付", "已发放", "已退款"].map((item) => `<option${item === filter ? " selected" : ""}>${item}</option>`).join("")}
          </select>
          <button class="btn btn-primary btn-sm" type="button" data-act="order-filter">查询</button>
          ${exportButton}
        </div>
      </div><div class="card-bd flush"><table class="data tight"><thead><tr>
        <th>订单号</th><th>用户</th><th>商品</th><th>金额</th><th>端</th><th>状态</th><th>时间</th><th>自动重试</th><th></th>
      </tr></thead><tbody>${rows || '<tr><td colspan="9">暂无订单</td></tr>'}</tbody></table></div></div>
    </div>${drawer}</div>`,
    true,
  );
}

function orderDrawer(row, admin) {
  const reissue = row.status === "发放异常" ? `<button class="btn btn-primary" type="button" data-act="reissue-open" data-id="${escapeHtml(row.id)}">补发</button>` : "";
  const refund =
    admin && row.status === "已发放"
      ? `<button class="btn btn-danger" type="button" data-act="refund-open" data-id="${escapeHtml(row.id)}">退款并回收权益</button>`
      : "";
  return `<aside class="drawer">
    <div class="drawer-hd">${escapeHtml(row.order_number)}<span class="sub">${escapeHtml(row.status)}</span></div>
    <p class="cs-hint"><a href="https://mpkf.weixin.qq.com/" target="_blank" rel="noopener noreferrer">回复请到微信小程序客服 ↗</a></p>
    <div class="drawer-bd"><div class="kv">
      <div class="k">用户</div><div class="v">${escapeHtml(row.phone || "—")}</div>
      <div class="k">商品</div><div class="v">${tierName(row.tier)} ${row.duration_days} 天 · <span class="money">${money(row.amount)}</span></div>
      <div class="k">端</div><div class="v">${escapeHtml(row.client || "—")}</div>
      <div class="k">自动重试</div><div class="v">${row.attempts == null ? "—" : `${row.attempts} 次`}</div>
      <div class="k">处理</div><div class="v">补发沿用订单上的档位和时长，不能改。退款后收回这笔订单发出的权益。</div>
    </div></div>
    <div class="drawer-ft">${reissue}${refund}</div>
  </aside>`;
}

async function renderContent() {
  if (state.me.role !== "admin") return denied();
  const [splash, notes, examples, programs] = await Promise.all([
    api("/api/v1/admin/content/splash"),
    api("/api/v1/admin/content/announcements"),
    api("/api/v1/admin/content/examples"),
    api("/api/v1/admin/content/programs"),
  ]);
  if (!splash.ok) {
    state.message = explain(splash.data, splash.status);
    shell("内容配置", "", false);
    return;
  }
  state.splash = splash.data;
  state.notes = notes.ok ? notes.data : [];
  state.examples = examples.ok ? examples.data : [];
  state.programs = programs.ok ? programs.data : [];
  const announcementRows = state.notes
    .map((row) => {
      const tone = row.state === "生效中" ? "tag-green" : "tag-gray";
      const freq = row.frequency === "every" ? "每次进入" : "只弹一次";
      const toggle = row.online
        ? `<button class="link" type="button" data-act="note-offline" data-id="${escapeHtml(row.id)}">下线</button>`
        : `<button class="link" type="button" data-act="note-online" data-id="${escapeHtml(row.id)}">上线</button>`;
      return `<tr><td><div class="ann-title">${escapeHtml(row.title)}</div><div class="ann-body">${escapeHtml(row.body)}</div></td>
        <td>${escapeHtml(row.button_text)}<br><span class="muted">${escapeHtml(row.jump_label || "无跳转")}</span></td>
        <td class="dates">${escapeHtml(ymd(row.starts_at))}<br>至 ${escapeHtml(ymd(row.ends_at))}</td>
        <td>${freq}</td><td><span class="tag ${tone}">${escapeHtml(row.state)}</span></td><td>${toggle}</td></tr>`;
    })
    .join("");
  const exampleCards = state.examples
    .map((row, index) => {
      const detected = row.detected_on ? ymd(row.detected_on) : "—";
      return `<div class="example-card"><div class="order">${index + 1}</div><div class="thumb"><span>示例</span></div><div style="flex:1">
        <h4>${escapeHtml(row.program_name)}</h4>
        <div class="muted">命中 ${row.platform_hits} 个平台 · 检测日期 ${escapeHtml(detected)}</div>
        <div style="margin-top:6px">${row.online ? '<span class="tag tag-green">已上线</span>' : '<span class="tag tag-gray">已下线</span>'}</div>
      </div><div style="display:flex;flex-direction:column;gap:6px">
        <button class="btn btn-default btn-sm" type="button" data-act="example-up" data-id="${escapeHtml(row.id)}">上移</button>
        <button class="btn btn-default btn-sm" type="button" data-act="example-down" data-id="${escapeHtml(row.id)}">下移</button>
        <button class="btn btn-default btn-sm" type="button" data-act="example-toggle" data-id="${escapeHtml(row.id)}" data-online="${row.online ? "0" : "1"}">${row.online ? "下线" : "上线"}</button>
      </div></div>`;
    })
    .join("");
  const options = state.programs
    .map((row) => `<option value="${escapeHtml(row.program_name)}">${escapeHtml(row.program_name)}</option>`)
    .join("");
  const poster = state.splash;
  shell(
    "内容配置",
    `<div class="card"><div class="card-hd">开屏海报 ${poster.enabled ? '<span class="tag tag-green">总开关：开</span>' : '<span class="tag tag-gray">总开关：关</span>'}</div>
      <div class="card-bd" style="display:flex;gap:24px">
        <div class="poster-frame"><div class="poster"><div class="brand">那豆在架</div><div>${escapeHtml(poster.slogan || "未填写口号")}</div><div class="skip">跳过 5</div></div></div>
        <div style="flex:1">
          <div class="field"><label>图片名称</label><input class="input" id="splash-image" value="${escapeHtml(poster.image_name || "")}"></div>
          <div class="field" style="margin-top:8px"><label>跳转说明</label><input class="input" id="splash-jump" value="${escapeHtml(poster.jump_label || "")}"></div>
          <div class="field" style="margin-top:8px"><label>口号</label><input class="input" id="splash-slogan" value="${escapeHtml(poster.slogan || "")}"></div>
          <p class="help">口号不得出现「永久免费」「全平台」。</p>
          <div style="margin-top:12px;display:flex;gap:8px">
            <button class="btn btn-primary" type="button" data-act="splash-save" data-enabled="1">保存并开启</button>
            <button class="btn btn-default" type="button" data-act="splash-save" data-enabled="0">关闭开屏</button>
          </div>
        </div>
      </div>
    </div>
    <div class="card"><div class="card-hd">公告</div><div class="card-bd flush"><table class="data"><thead><tr>
      <th>公告</th><th>按钮 / 跳转</th><th>生效期间</th><th>频率</th><th>状态</th><th></th>
    </tr></thead><tbody>${announcementRows || '<tr><td colspan="6">暂无公告</td></tr>'}</tbody></table></div>
      <div class="card-bd"><div class="filters">
        <input class="input w-md" id="ann-title" placeholder="标题">
        <input class="input w-lg" id="ann-body" placeholder="正文，不超过 200 字">
        <input class="input w-sm" id="ann-button" value="我知道了">
        <select class="select w-sm" id="ann-freq"><option value="once">只弹一次</option><option value="every">每次进入</option></select>
        <button class="btn btn-primary" type="button" data-act="ann-create">新建公告</button>
      </div></div>
    </div>
    <div class="card"><div class="card-hd">首页示例记录<span class="sub">${state.examples.length} / 5</span></div><div class="card-bd">
      <p class="help" style="margin-top:0">每张卡片须醒目标注「示例」。最多 5 条。满 5 条后需先下线或移除一条再添加。</p>
      <div style="display:flex;flex-direction:column;gap:10px">${exampleCards || "暂无示例"}</div>
      <div style="margin-top:12px;display:flex;gap:8px">
        <select class="select w-md" id="example-program">${options}</select>
        <button class="btn btn-primary" type="button" data-act="example-add">添加示例</button>
      </div>
    </div></div>`,
    false,
  );
}

const ACTION_LABELS = {
  login: "登录",
  logout: "退出",
  unlock: "解锁登录",
  lock: "登录锁定",
  create_staff: "新建账号",
  ban_user: "封禁",
  unban_user: "解禁",
  grant_membership: "手动开通会员",
  reissue_entitlement: "发放补发",
  refund_order: "退款并回收权益",
  add_evidence_note: "沟通备注",
  confirm_payment: "确认线下收款",
  record_delivery: "记录线下交付",
  accept_evidence: "接单",
  complete_evidence: "确认完成",
  close_evidence: "关闭申请",
  cancel_evidence: "取消申请",
  set_quote: "填写报价",
  fail_evidence: "标记失败",
  create_evidence_request: "新取证申请",
  void_invite: "作废邀请码",
  create_invites: "生成邀请码",
  update_splash: "保存开屏",
  save_announcement: "保存公告",
  offline_announcement: "公告下线",
  online_announcement: "公告上线",
  add_example: "添加首页示例",
  offline_example: "首页示例下线",
  online_example: "首页示例上线",
  move_example: "调整示例顺序",
  import_index: "数据导入",
};

function auditValue(key, item) {
  if (key === "role") return item === "admin" ? "管理员" : "客服";
  if (key === "tier") return tierName(item);
  if (key === "status" && item === "success") return "成功";
  if (key === "status" && item === "failed") return "失败";
  if (typeof item === "boolean") return item ? "是" : "否";
  return item == null ? "—" : String(item);
}

function auditText(value) {
  if (value == null || value === "") return "—";
  if (typeof value !== "object") return escapeHtml(String(value));
  const labels = {
    status: "状态",
    tier: "档位",
    duration_days: "天数",
    reason: "原因",
    days: "天数",
    channel: "渠道",
    count: "数量",
    code: "邀请码",
    text: "内容",
    username: "账号",
    role: "角色",
    application_number: "申请号",
    quote_amount: "报价",
    payment_amount: "收款",
    delivery_method: "交付方式",
    delivery_note: "说明",
    failure_reason: "失败原因",
    refund_method: "退款方式",
    refund_amount: "退款金额",
    attempts: "尝试次数",
    new_rows: "新增行数",
    error: "说明",
    title: "标题",
    program_name: "节目",
    sort_order: "排序",
    amount: "金额",
  };
  const parts = [];
  for (const [key, item] of Object.entries(value)) {
    if (!labels[key] || (item && typeof item === "object")) continue;
    parts.push(`${labels[key]} ${auditValue(key, item)}`);
  }
  return escapeHtml(parts.join("；") || "—");
}

const LOG_TYPES = [
  ["", "全部对象"],
  ["evidence_request", "取证申请"],
  ["evidence_note", "沟通备注"],
  ["order", "订单"],
  ["user", "用户"],
  ["entitlement", "会员权益"],
  ["invite_code", "邀请码"],
  ["staff_account", "账号"],
  ["announcement", "公告"],
  ["splash", "开屏"],
  ["homepage_example", "首页示例"],
];

async function renderLogs() {
  if (state.me.role !== "admin") return denied();
  const page = state.logPage || 0;
  const params = new URLSearchParams();
  params.set("limit", "20");
  params.set("offset", String(page * 20));
  params.set("paged", "1");
  if (state.logActor) params.set("actor", state.logActor);
  if (state.logQuery) params.set("q", state.logQuery);
  if (state.logType) params.set("object_type", state.logType);
  if (state.logFrom) params.set("day_from", state.logFrom);
  if (state.logTo) params.set("day_to", state.logTo);
  const listed = await api(`/api/v1/admin/audit-logs?${params.toString()}`);
  if (!listed.ok) {
    state.message = explain(listed.data, listed.status);
    shell("操作日志", "", false);
    return;
  }
  state.logs = listed.data.items || [];
  state.logTotal = listed.data.total || 0;
  const rows = state.logs
    .map(
      (row) => `<tr>
        <td class="nowrap">${escapeHtml(fmt(row.created_at))}</td>
        <td class="nowrap">${escapeHtml(row.actor_username || "—")}</td>
        <td class="nowrap">${escapeHtml(ACTION_LABELS[row.action] || "操作")}</td>
        <td>${escapeHtml(row.object_label || "记录")}</td>
        <td>${row.before_text ? escapeHtml(row.before_text) : auditText(row.before)}</td>
        <td>${row.after_text ? escapeHtml(row.after_text) : auditText(row.after)}</td>
      </tr>`,
    )
    .join("");
  const typeOptions = LOG_TYPES.map(
    ([value, label]) =>
      `<option value="${value}"${value === (state.logType || "") ? " selected" : ""}>${label}</option>`,
  ).join("");
  const lastPage = Math.max(0, Math.ceil(state.logTotal / 20) - 1);
  const pages = [];
  for (let index = 0; index <= lastPage; index += 1) {
    const current = index === page ? " btn-primary" : " btn-default";
    pages.push(
      `<button class="btn btn-sm${current}" type="button" data-act="log-page" data-page="${index}">${index + 1}</button>`,
    );
  }
  shell(
    "操作日志",
    `<div class="card"><div class="card-hd">筛选</div><div class="card-bd"><div class="filter-bar labeled">
      <div class="field"><label for="log-actor">操作人</label><input class="input w-sm" id="log-actor" value="${escapeHtml(state.logActor || "")}"></div>
      <div class="field"><label for="log-type">对象</label><select class="select w-sm" id="log-type">${typeOptions}</select></div>
      <div class="field"><label>时间</label><span class="range"><input class="input w-date" id="log-from" inputmode="numeric" placeholder="YYYY-MM-DD" value="${escapeHtml(state.logFrom || "")}"><span class="tilde">~</span><input class="input w-date" id="log-to" inputmode="numeric" placeholder="YYYY-MM-DD" value="${escapeHtml(state.logTo || "")}"></span></div>
      <div class="field"><label for="log-query">关键词</label><input class="input w-md" id="log-query" value="${escapeHtml(state.logQuery || "")}"></div>
      <button class="btn btn-primary" type="button" data-act="log-search">查询</button>
    </div></div></div>
    <div class="card"><div class="card-hd">操作记录<span class="sub">共 ${state.logTotal} 条</span></div>
      <div class="card-bd flush"><table class="data"><thead><tr>
        <th>时间</th><th>操作人</th><th>操作</th><th>对象</th><th>前值</th><th>后值</th>
      </tr></thead><tbody>${rows || '<tr><td colspan="6">暂无记录</td></tr>'}</tbody></table></div>
      <div class="card-bd pager">${pages.join("") || '<span class="help" style="margin:0">1</span>'}</div>
    </div>`,
    false,
  );
}

function runLabel(status) {
  if (status === "success") return '<span class="tag tag-green tag-dot">成功</span>';
  if (status === "failed") return '<span class="tag tag-red tag-dot">失败</span>';
  return '<span class="tag tag-orange tag-dot">进行中</span>';
}

async function renderIndex() {
  if (state.me.role !== "admin") return denied();
  const status = state.index && state.index.runs ? { ok: true, data: state.index } : await api("/api/v1/admin/index/status");
  if (!status.ok) {
    state.message = explain(status.data, status.status);
    shell("数据与索引", "", false);
    return;
  }
  state.index = status.data;
  const data = status.data;
  const unknown = (data.unrecognized || [])
    .map(
      (row) => `<tr><td class="mono">${escapeHtml(row.filename || row.task_id)}</td><td>${escapeHtml(row.reason)}</td><td>${platformHtml(row.display)}</td></tr>`,
    )
    .join("");
  const runs = (data.runs || [])
    .map(
      (row) => `<tr class="${row.status === "failed" ? "overdue" : ""}">
        <td class="nowrap">${escapeHtml(fmt(row.started_at))}</td>
        <td>${runLabel(row.status)}</td>
        <td>${row.status === "success" ? row.new_rows : "—"}</td>
        <td>${escapeHtml(row.error_summary && !/[A-Za-z]/.test(row.error_summary) ? row.error_summary : row.status === "success" ? "增量完成" : "本次没有写入")}</td>
      </tr>`,
    )
    .join("");
  const alert = data.stale
    ? `<div class="alert-block"><h2>索引断更告警：已超过 2 天没有成功更新</h2>
        <p>已通知管理员</p></div>`
    : "";
  shell(
    "数据与索引",
    `${alert}
    <div class="stats cols-4">
      <div class="stat"><div class="k">索引最后更新时间</div><div class="v${data.stale ? " danger" : ""}" style="font-size:18px">${escapeHtml(fmt(data.last_successful_index_at))}</div></div>
      <div class="stat"><div class="k">最近一次运行</div><div class="v" style="font-size:18px">${escapeHtml(data.last_run ? fmt(data.last_run.started_at) : "—")}</div></div>
      <div class="stat"><div class="k">上次成功新增行数</div><div class="v">${data.last_success_new_rows ?? "—"}</div></div>
      <div class="stat"><div class="k">平台未识别</div><div class="v">${(data.unrecognized || []).length}</div></div>
    </div>
    <div class="grid-2">
      <div class="card" style="margin:0"><div class="card-hd">数据导入</div><div class="card-bd">
        <label class="btn btn-default" for="import-file">选择任务导出表</label>
        <input id="import-file" type="file" accept=".csv,text/csv" style="display:none">
        <span class="muted" id="import-name">未选择文件</span>
        <button class="btn btn-primary" type="button" data-act="import-index" style="margin-top:10px">开始导入</button>
      </div></div>
      <div class="card" style="margin:0"><div class="card-hd">平台未识别 · 只读告警</div><div class="card-bd flush">
        <table class="data tight"><thead><tr><th>文件名</th><th>原因</th><th>展示</th></tr></thead>
        <tbody>${unknown || '<tr><td colspan="3">暂无</td></tr>'}</tbody></table>
        <p class="help" style="padding:8px 12px 12px;margin:0">这些记录仍计为已命中，但不计入「涉及 N 个平台」。这里不能改。</p>
      </div></div>
    </div>
    <div class="card" style="margin-top:16px"><div class="card-hd">定时任务运行日志</div><div class="card-bd flush">
      <table class="data"><thead><tr><th>运行时间</th><th>结果</th><th>新增行数</th><th>说明</th></tr></thead>
      <tbody>${runs || '<tr><td colspan="4">暂无运行</td></tr>'}</tbody></table>
    </div></div>`,
    false,
  );
}

function showModal(html) {
  state.modal = `<div class="modal-mask show">${html}</div>`;
  return draw();
}

async function draw() {
  const token = ++generation;
  if (!location.hash) {
    history.replaceState(null, "", sessionStorage.getItem("nadou-token") ? "#/w02" : "#/login");
  }
  const route = location.hash;
  if (route === "#/login" || route === "#/w01") {
    state.me = null;
    renderLogin(state.message);
    state.message = "";
    return;
  }
  if (!state.me) {
    const me = await api("/api/v1/admin/auth/me");
    if (token !== generation) return;
    if (!me.ok) {
      renderLogin("");
      return;
    }
    state.me = me.data;
  }
  await refreshAlerts();
  if (token !== generation) return;
  document.title = "那豆在架 工作台";
  if (route === "#/w03") await renderInvites();
  else if (route === "#/w04") await renderUsers();
  else if (route === "#/w05") await renderOrders();
  else if (route === "#/w06") await renderContent();
  else if (route === "#/w08") await renderLogs();
  else if (route === "#/w09") await renderIndex();
  else if (route === "#/w02") await renderEvidence();
  else {
    shell("页面不存在", '<div class="card"><div class="card-bd">没有这个页面。</div></div>', false);
  }
  state.message = "";
}

async function afterChange(ok, status, data) {
  if (!ok) {
    state.message = explain(data, status);
    state.modal = null;
    await draw();
    return;
  }
  state.modal = null;
  state.message = "";
  state.index = null;
  await draw();
}

function shanghaiStamp(text) {
  const match = /^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})$/.exec(text);
  if (!match) return "";
  return `${match[1]}T${match[2]}:${match[3]}:00+08:00`;
}

async function reloadUser() {
  if (!state.user) return;
  const detail = await api(`/api/v1/admin/users/${state.user.id}`);
  if (detail.ok) state.user = detail.data;
}

function value(id) {
  const node = document.querySelector(id);
  return node ? node.value.trim() : "";
}

async function onClick(event) {
  const node = event.target.closest("[data-act]");
  if (!node) return;
  const act = node.dataset.act;
  const id = node.dataset.id;
  if (act === "logout") {
    await api("/api/v1/admin/auth/logout", { method: "POST" });
    sessionStorage.removeItem("nadou-token");
    const next = new URL("/admin/", location.origin);
    next.searchParams.set("reset", String(Date.now()));
    next.hash = "/login";
    location.replace(next.href);
    return;
  }
  if (act === "evidence-filter") {
    const fromDay = value("#evidence-from");
    const toDay = value("#evidence-to");
    if ((fromDay && !dayText(fromDay)) || (toDay && !dayText(toDay))) {
      state.message = "日期请按 YYYY-MM-DD 填写。";
      await draw();
      return;
    }
    state.evidenceQuery = value("#evidence-query");
    state.evidenceStatus = value("#evidence-status");
    state.evidenceType = value("#evidence-type");
    state.evidenceFrom = fromDay;
    state.evidenceTo = toDay;
    const vipBox = document.querySelector("#evidence-vip");
    state.evidenceVip = Boolean(vipBox && vipBox.checked);
    await draw();
    return;
  }
  if (act === "close-drawer") {
    state.evidenceId = null;
    state.orderId = null;
    state.detail = null;
    await draw();
    return;
  }
  if (act === "open-evidence") {
    state.evidenceId = id;
    await draw();
    return;
  }
  if (act === "open-order") {
    state.orderId = id;
    await draw();
    return;
  }
  if (act === "cancel" || act === "complete" || act === "close") {
    const result = await api(`/api/v1/admin/evidence-requests/${id}/${act}`, { method: "POST" });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "accept") {
    const result = await api(`/api/v1/admin/evidence-requests/${id}/accept`, { method: "POST" });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "quote" || act === "pay") {
    const typed = value("#quote-amount");
    const amount = Number(typed || node.dataset.amount || "");
    if (!amount) {
      state.message = "请填写金额。";
      await draw();
      return;
    }
    const path = act === "quote" ? "quote" : "confirm-payment";
    const result = await api(`/api/v1/admin/evidence-requests/${id}/${path}`, {
      method: "POST",
      json: { amount },
    });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "deliver") {
    const method = value("#deliver-method");
    const note = value("#deliver-note");
    if (method === "其他" && !note) {
      state.message = "交付方式选「其他」时必须填写说明。";
      await draw();
      return;
    }
    const result = await api(`/api/v1/admin/evidence-requests/${id}/deliver`, {
      method: "POST",
      json: { method, note: note || null },
    });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "note") {
    const text = value("#note-text");
    if (!text) {
      state.message = "请填写沟通备注。";
      await draw();
      return;
    }
    const result = await api(`/api/v1/admin/evidence-requests/${id}/notes`, {
      method: "POST",
      json: { text },
    });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "fail-open") {
    const row = state.detail;
    const paid = row && row.status === "取证中";
    const help = paid
      ? "该申请已确认线下收款（取证中）。无法交付时须记录退款方式和金额，金额默认等于已确认的线下收款。确认后记为取证失败并关闭。"
      : "该申请尚未确认收款，不需要退款。「已受理」和「待付款（线下）」都不出现退款字段。失败原因必填。确认后记为取证失败并关闭。";
    const refund = paid
      ? `<div class="field" style="margin-top:12px"><label>退款方式<span class="req">*</span></label><select class="select" id="refund-method" style="width:100%"><option>原路退回</option><option>银行转账</option><option>微信转账</option><option>其他</option></select></div>
         <div class="field" style="margin-top:12px"><label>退款金额<span class="req">*</span></label><input class="input" id="refund-amount" value="${escapeHtml(row.payment_amount || "")}"></div>
         <div class="field" style="margin-top:12px"><label>退款记录<span class="req">*</span></label><input class="input" id="refund-note" placeholder="请填写退款凭证摘要，此项必填"></div>`
      : "";
    await showModal(`<div class="modal"><div class="modal-hd">标记失败</div><div class="modal-bd">
      <p class="help" style="margin-top:0">${help}</p>
      <p class="help fail-error" id="fail-error" hidden>请补全标红的必填项。</p>
      <div class="field"><label>失败原因<span class="req">*</span></label><textarea class="textarea" id="fail-reason" rows="3" placeholder="请填写失败原因，此项必填"></textarea></div>
      ${refund}
    </div><div class="modal-ft">
      <button class="btn btn-default" type="button" data-act="modal-close">取消</button>
      <button class="btn btn-danger-solid" type="button" data-act="fail" data-id="${escapeHtml(id)}">确认失败并关闭</button>
    </div></div>`);
    return;
  }
  if (act === "fail") {
    const paid = state.detail && state.detail.status === "取证中";
    const missing = ["#fail-reason"];
    if (paid) missing.push("#refund-method", "#refund-amount", "#refund-note");
    let blocked = false;
    for (const selector of missing) {
      const field = document.querySelector(selector);
      if (!field) continue;
      const empty = !field.value.trim();
      field.classList.toggle("invalid", empty);
      blocked = blocked || empty;
    }
    const error = document.querySelector("#fail-error");
    if (error) error.hidden = !blocked;
    if (blocked) return;
    const payload = { reason: value("#fail-reason"), and_close: true };
    if (paid) {
      payload.refund_method = value("#refund-method");
      payload.refund_amount = Number(value("#refund-amount"));
      payload.refund_note = value("#refund-note");
    }
    const result = await api(`/api/v1/admin/evidence-requests/${id}/fail`, { method: "POST", json: payload });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "modal-close") {
    state.modal = null;
    await draw();
    return;
  }
  if (act === "invite-create") {
    const count = Number(value("#invite-count"));
    const channel = value("#invite-channel");
    const day = value("#invite-until");
    if (!count || !channel || !dayText(day)) {
      state.message = "请填写数量、渠道，有效期用 YYYY-MM-DD。";
      await draw();
      return;
    }
    const result = await api("/api/v1/admin/invite-codes", {
      method: "POST",
      json: { count, channel, expires_at: `${day}T23:59:00+08:00` },
    });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "void-open") {
    await showModal(`<div class="modal"><div class="modal-hd">确认作废</div><div class="modal-bd"><p style="margin:0">确认作废邀请码 <strong class="mono">${escapeHtml(node.dataset.code)}</strong> 吗？作废后不可再兑换，并写入操作日志。</p></div>
      <div class="modal-ft"><button class="btn btn-default" type="button" data-act="modal-close">取消</button>
      <button class="btn btn-danger-solid" type="button" data-act="void" data-id="${escapeHtml(id)}">确认作废</button></div></div>`);
    return;
  }
  if (act === "void") {
    const result = await api(`/api/v1/admin/invite-codes/${id}/void`, { method: "POST" });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "invite-export") {
    await download("/api/v1/admin/invite-codes/export", "invites.csv");
    return;
  }
  if (act === "user-search") {
    let query = value("#user-query");
    if (state.me.role !== "admin" && state.userQuery && query === maskPhone(state.userQuery)) {
      query = state.userQuery;
    }
    state.userQuery = query;
    const found = await api(`/api/v1/admin/users?q=${encodeURIComponent(query)}`);
    if (!found.ok || !found.data[0]) {
      state.user = null;
      state.message = found.ok ? "没有找到这个用户。" : explain(found.data, found.status);
      await draw();
      return;
    }
    const detail = await api(`/api/v1/admin/users/${found.data[0].id}`);
    state.user = detail.ok ? detail.data : null;
    state.locks = [];
    await draw();
    return;
  }
  if (act === "grant") {
    if (!state.user) return;
    const result = await api(`/api/v1/admin/users/${state.user.id}/membership`, {
      method: "POST",
      json: { tier: value("#grant-tier"), days: Number(value("#grant-days")), reason: value("#grant-reason") },
    });
    if (result.ok) await reloadUser();
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "ban-open") {
    if (!state.user) return;
    await showModal(`<div class="modal"><div class="modal-hd">封禁账号</div><div class="modal-bd">
      <div class="field"><label>封禁类型</label><select class="select" id="ban-type" style="width:100%"><option value="temporary">临时封禁</option><option value="permanent">永久封禁</option></select></div>
      <div class="field" style="margin-top:12px"><label>结束时间</label><input class="input" id="ban-until" placeholder="YYYY-MM-DD HH:mm"></div>
      <div class="field" style="margin-top:12px"><label>封禁原因<span class="req">*</span></label><textarea class="textarea" id="ban-reason" rows="3" placeholder="请填写封禁原因，此项必填"></textarea></div>
    </div><div class="modal-ft">
      <button class="btn btn-default" type="button" data-act="modal-close">取消</button>
      <button class="btn btn-danger-solid" type="button" data-act="ban" data-id="${escapeHtml(state.user.id)}">确认封禁</button>
    </div></div>`);
    return;
  }
  if (act === "ban") {
    const kind = value("#ban-type");
    const payload = { type: kind, reason: value("#ban-reason") };
    if (kind === "temporary") {
      const until = shanghaiStamp(value("#ban-until"));
      if (!until) {
        const field = document.querySelector("#ban-until");
        if (field) field.classList.add("invalid");
        return;
      }
      payload.until = until;
    }
    const result = await api(`/api/v1/admin/users/${id}/ban`, { method: "POST", json: payload });
    if (result.ok) await reloadUser();
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "unban") {
    const result = await api(`/api/v1/admin/users/${id}/unban`, { method: "POST" });
    if (result.ok) await reloadUser();
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "unlock") {
    const result = await api(`/api/v1/admin/staff/${id}/unlock`, { method: "POST" });
    state.locks = [];
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "staff-create") {
    const result = await api("/api/v1/admin/staff", {
      method: "POST",
      json: { username: value("#staff-name"), password: value("#staff-pass"), role: value("#staff-role") },
    });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "order-filter") {
    state.orderQuery = value("#order-query");
    state.orderFilter = value("#order-filter");
    await draw();
    return;
  }
  if (act === "order-export") {
    await download("/api/v1/admin/orders/export", "orders.csv");
    return;
  }
  if (act === "reissue-open") {
    const row = state.orders.find((item) => item.id === id);
    await showModal(`<div class="modal"><div class="modal-hd">确认补发</div><div class="modal-bd">
      <div class="kv"><div class="k">订单号</div><div class="v mono">${escapeHtml(row ? row.order_number : "")}</div>
      <div class="k">将发放的权益</div><div class="v">${tierName(row ? row.tier : "")} ${row ? row.duration_days : ""} 天（取自订单，不可修改）</div></div>
      <p class="help">按订单号去重，重复点击不会重复开通。</p>
    </div><div class="modal-ft">
      <button class="btn btn-default" type="button" data-act="modal-close">取消</button>
      <button class="btn btn-primary" type="button" data-act="reissue" data-id="${escapeHtml(id)}">确认补发</button>
    </div></div>`);
    return;
  }
  if (act === "reissue") {
    const result = await api(`/api/v1/admin/orders/${id}/reissue`, { method: "POST", json: {} });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "refund-open") {
    await showModal(`<div class="modal"><div class="modal-hd">退款并回收权益</div><div class="modal-bd">
      <div class="field"><label>原因</label><textarea class="textarea" id="refund-reason" rows="3"></textarea></div>
    </div><div class="modal-ft">
      <button class="btn btn-default" type="button" data-act="modal-close">取消</button>
      <button class="btn btn-danger-solid" type="button" data-act="refund" data-id="${escapeHtml(id)}">确认退款</button>
    </div></div>`);
    return;
  }
  if (act === "refund") {
    const result = await api(`/api/v1/admin/orders/${id}/refund`, {
      method: "POST",
      json: { reason: value("#refund-reason") },
    });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "splash-save") {
    const result = await api("/api/v1/admin/content/splash", {
      method: "PUT",
      json: {
        enabled: node.dataset.enabled === "1",
        image_name: value("#splash-image"),
        jump_label: value("#splash-jump"),
        slogan: value("#splash-slogan"),
      },
    });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "ann-create") {
    const start = new Date();
    const end = new Date(start.getTime() + 14 * 24 * 3600 * 1000);
    const result = await api("/api/v1/admin/content/announcements", {
      method: "POST",
      json: {
        title: value("#ann-title"),
        body: value("#ann-body"),
        button_text: value("#ann-button") || "我知道了",
        jump_label: null,
        starts_at: start.toISOString(),
        ends_at: end.toISOString(),
        frequency: value("#ann-freq") || "once",
        online: true,
      },
    });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "note-offline" || act === "note-online") {
    const result = await api(`/api/v1/admin/content/announcements/${id}/online`, {
      method: "POST",
      json: { online: act === "note-online" },
    });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "example-add") {
    const result = await api("/api/v1/admin/content/examples", {
      method: "POST",
      json: { program_name: value("#example-program"), record_id: null },
    });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "example-up" || act === "example-down") {
    const result = await api(`/api/v1/admin/content/examples/${id}/move`, {
      method: "POST",
      json: { direction: act === "example-up" ? "up" : "down" },
    });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "example-toggle") {
    const result = await api(`/api/v1/admin/content/examples/${id}/online`, {
      method: "POST",
      json: { online: node.dataset.online === "1" },
    });
    await afterChange(result.ok, result.status, result.data);
    return;
  }
  if (act === "log-search" || act === "log-page") {
    const fromDay = value("#log-from");
    const toDay = value("#log-to");
    if ((fromDay && !dayText(fromDay)) || (toDay && !dayText(toDay))) {
      state.message = "日期请按 YYYY-MM-DD 填写。";
      await draw();
      return;
    }
    state.logActor = value("#log-actor");
    state.logQuery = value("#log-query");
    state.logType = value("#log-type");
    state.logFrom = fromDay;
    state.logTo = toDay;
    if (act === "log-page") {
      const last = Math.max(0, Math.ceil((state.logTotal || 0) / 20) - 1);
      const picked = Number(node.dataset.page);
      state.logPage = Number.isInteger(picked) ? Math.min(last, Math.max(0, picked)) : 0;
    } else {
      state.logPage = 0;
    }
    await renderLogs();
    return;
  }
  if (act === "import-index") {
    const input = document.querySelector("#import-file");
    const file = input && input.files ? input.files[0] : null;
    if (!file) {
      state.message = "请选择任务导出表。";
      await draw();
      return;
    }
    const csv = await file.text();
    const result = await api("/api/v1/admin/index/import", { method: "POST", json: { csv } });
    state.index = null;
    await afterChange(result.ok, result.status, result.data);
  }
}

async function download(path, filename) {
  const result = await api(path);
  if (!result.ok) {
    state.message = explain(result.data, result.status);
    await draw();
    return;
  }
  const blob = new Blob([result.data], { type: "text/csv" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (state.modal) {
    state.modal = null;
    draw();
    return;
  }
  if (state.evidenceId || state.orderId) {
    state.evidenceId = null;
    state.orderId = null;
    state.detail = null;
    draw();
  }
});

document.body.addEventListener("click", (event) => {
  onClick(event).catch(() => {
    state.message = "操作没有完成，请稍后再试。";
    draw();
  });
});

document.body.addEventListener("submit", (event) => {
  const form = event.target.closest("[data-act='login']");
  if (!form) return;
  event.preventDefault();
  const data = new FormData(form);
  api("/api/v1/admin/auth/login", {
    method: "POST",
    json: { username: String(data.get("username") || ""), password: String(data.get("password") || "") },
  })
    .then((result) => {
      if (!result.ok) {
        renderLogin(explain(result.data, result.status));
        return;
      }
      sessionStorage.setItem("nadou-token", result.data.token);
      state.me = null;
      state.message = "";
      location.hash = "#/w02";
    })
    .catch(() => renderLogin("登录没有完成，请稍后再试。"));
});

window.addEventListener("hashchange", () => {
  draw().catch(() => {
    state.message = "页面没有载入，请刷新。";
  });
});

draw().catch(() => {
  renderLogin("页面没有载入，请刷新。");
});

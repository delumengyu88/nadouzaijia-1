var catalog = require('./catalog')
var shape = require('./shape')
var state = require('./state')
var text = require('../utils/text')
var date = require('../utils/date')
var xlsx = require('../utils/xlsx')

var PRODUCTS = {
  membership_vip: { title: 'VIP 会员 30 天', card: 'VIP 会员', price: 599, days: 30, blurb: '有效期 30 天' },
  membership_regular: { title: '普通会员 30 天', card: '普通会员', price: 299, days: 30, blurb: '有效期 30 天' },
  single_program_30d: { title: '单节目解锁', card: '本节目 · 含 30 天以上记录', price: 199, days: 7, blurb: '在线查看 7 天' },
  single_program_90d: { title: '单节目解锁', card: '本节目 · 含 90 天以上记录', price: 99, days: 7, blurb: '在线查看 7 天' },
  screenshot: { title: '单张截图', card: '单张截图', price: 29, days: 7, blurb: '有效期 7 天' }
}

var STATUS_TEXT = {
  pending: '待支付',
  paying: '支付中',
  confirming: '支付确认中',
  granting: '权益发放中',
  granted: '已发放',
  failed: '失败',
  cancelled: '已取消',
  grant_error: '发放异常'
}

var CODES = {
  A8K3N2P7: 'ok',
  C9D5E7F2: 'expired',
  D4E6F8G3: 'used'
}

var EVIDENCE_TEXT = {
  submitted: '',
  accepted: '',
  pay: '请按客服指引完成线下付款，付款不在小程序内完成。',
  working: '',
  delivered: '材料已线下交付，请确认查收。',
  done: '',
  failed: '',
  closed: '',
  cancelled: ''
}

var EVIDENCE_BADGE = require('./evidence').badge

function save() { state.save() }
function acc() { return state.account() }

function grantsOf(account, programName) {
  var g = { vip: false, regular: false, program30: false, program90: false }
  if (!account) return g
  if (date.active(account.paidVipExpiry)) g.vip = true
  if (date.active(account.inviteExpiry)) g.regular = true
  if (date.active(account.paidRegularExpiry)) g.regular = true
  return g
}

function tierOf(account) {
  if (!account) return 'guest'
  if (date.active(account.paidVipExpiry)) return 'vip'
  if (date.active(account.inviteExpiry) || date.active(account.paidRegularExpiry)) return 'regular'
  return 'free'
}

function bucket(days) {
  if (days < 30) return 'lt_30'
  if (days < 90) return 'd30_89'
  if (days < 180) return 'd90_179'
  return 'ge_180'
}

function decide(days, g) {
  var age = bucket(days)
  if (days < 30) {
    return { locked: true, age_bucket: age, lock_reason: '有最新记录，可委托取证' }
  }
  var memberFloor = g.vip ? 30 : (g.regular ? 90 : null)
  var programFloor = g.program30 ? 30 : (g.program90 ? 90 : null)
  var membershipSees = memberFloor !== null && days >= memberFloor
  var programSees = programFloor !== null && days >= programFloor
  if (membershipSees || programSees) {
    return {
      locked: false,
      age_bucket: age,
      access: 'full',
      can_download_report: membershipSees,
      can_download_screenshot: membershipSees
    }
  }
  if (days >= 180) {
    return {
      locked: false,
      age_bucket: age,
      access: 'lowres',
      can_download_report: false,
      can_download_screenshot: false
    }
  }
  return {
    locked: true,
    age_bucket: age,
    lock_reason: age === 'd30_89' ? '此记录需 VIP 或单节目 30 天档查看' : '此记录需普通会员或单节目解锁查看'
  }
}

function sign(variant, id) {
  return 'signed://derived/' + variant + '/' + id + '?uid=' + (state.userId() || 'anon')
}

function missBody(name) {
  return shape.sanitizeSearch({
    program_name: text.displayOf(name),
    outcome: 'miss',
    platform_count: 0,
    unverified_source_count: 0,
    locked_summary: shape.emptySummary(),
    records: [],
    locked_window: { count: 0, start: '', end: '' }
  })
}

function lockedBody(foundName, meta) {
  var count = meta.lockedCount || meta.rows.length
  var platforms = meta.lockedPlatforms
  if (!platforms) {
    var set = {}
    meta.rows.forEach(function (row) {
      if (row.platform) set[row.platform] = 1
    })
    platforms = Object.keys(set).length
  }
  var start = meta.lockedStart || (meta.rows.length ? meta.rows[meta.rows.length - 1].date : '')
  var end = meta.lockedEnd || (meta.rows.length ? meta.rows[0].date : '')
  return shape.sanitizeSearch({
    program_name: foundName,
    outcome: 'hit',
    platform_count: platforms,
    unverified_source_count: 0,
    locked_summary: {
      locked_total: count,
      by_required_tier: { member: 0, vip: count, evidence_only: 0 },
      unlockable_total: count,
      cheapest_full_unlock: 'single_program_30d',
      unlocks_by_product: {}
    },
    records: [{
      record_id: 'lk' + String(count),
      age_bucket: 'd30_89',
      locked: true,
      lock_reason: '此记录需 VIP 或单节目 30 天档查看'
    }],
    locked_window: { count: count, start: start, end: end }
  })
}

function searchRaw(name, options) {
  if (text.isStopped(name)) return missBody(name)
  var key = text.foldKey(name)
  var foundName = ''
  Object.keys(catalog.programs).forEach(function (title) {
    if (text.foldKey(title) === key) foundName = title
  })
  if (!foundName) return missBody(name)
  var meta = catalog.programs[foundName]
  var force = options && options.example
  var g = force
    ? { vip: true, regular: true, program30: true, program90: true }
    : grantsOf(acc(), foundName)
  if (options && options.locked) return lockedBody(foundName, meta)
  var canOpen = force || g.vip || g.regular || g.program30 || g.program90
  if (!canOpen) return lockedBody(foundName, meta)
  var platforms = {}
  var unverified = 0
  var records = []
  var membership = !!(g.vip || g.regular)
  meta.rows.forEach(function (row) {
    if (row.platform) platforms[row.platform] = 1
    else unverified += 1
    var open = {
      record_id: row.id,
      age_bucket: 'ge_180',
      locked: false,
      access: row.low ? 'lowres' : 'full',
      can_download_report: membership && !row.low,
      can_download_screenshot: membership && !row.low,
      checked_at: row.date,
      category: row.category,
      platform_subtype: row.sub,
      thumbnail_url: sign(row.low ? 'lowres' : 'card', row.id)
    }
    if (row.platform) open.platform = row.platform
    if (!row.low) open.hd_url = sign('hd', row.id)
    records.push(open)
  })
  return shape.sanitizeSearch({
    program_name: foundName,
    outcome: 'hit',
    platform_count: Object.keys(platforms).length,
    unverified_source_count: unverified,
    locked_summary: shape.emptySummary(),
    records: records,
    locked_window: { count: 0, start: '', end: '' },
    example: !!force
  })
}

function metaOf(name) {
  var key = text.foldKey(name)
  var found = ''
  Object.keys(catalog.programs).forEach(function (title) {
    if (text.foldKey(title) === key) found = title
  })
  if (!found) return { name: text.displayOf(name) }
  return { name: found }
}

function latestDate(report) {
  var latest = ''
  ;(report.records || []).forEach(function (rec) {
    if (rec.locked || !rec.checked_at) return
    var day = String(rec.checked_at).slice(0, 10)
    if (day > latest) latest = day
  })
  return latest
}

function exampleSummary(name) {
  var report = searchRaw(name, { example: true })
  var meta = metaOf(name)
  var count = 0
  report.records.forEach(function (rec) {
    if (rec.age_bucket !== 'lt_30') count += 1
  })
  var day = latestDate(report)
  var sub = '命中 ' + count + ' 条记录' + (day ? ' · ' + day : '')
  report.line = '涉及 ' + report.platform_count + ' 个平台'
  report.sub = sub
  return { name: meta.name, line: report.line, sub: sub, report: report }
}

function homeExamples() {
  var factsOf = require('../utils/present').exampleFacts
  var cards = []
  catalog.examples.forEach(function (name) {
    var card = exampleSummary(name)
    var facts = factsOf(card.report.records)
    if (!facts.hit) return
    cards.push({ name: card.name, line: facts.line, sub: facts.sub, image: facts.image, illustrated: !facts.image })
  })
  return cards
}

function runningQuery(account) {
  var found = null
  ;(account ? account.queries : []).forEach(function (query) {
    if (query.status === 'running') found = query
  })
  return found
}

function unseenQuery(account) {
  var found = null
  ;(account ? account.queries : []).forEach(function (query) {
    if (query.status === 'done' && !query.seen) found = query
  })
  return found
}

function summaryText(query) {
  var view = queryView(query)
  return view.head
}

function homeState() {
  var session = state.load()
  var account = acc()
  var running = runningQuery(account)
  var unseen = unseenQuery(account)
  var tier = tierOf(account)
  var banner = ''
  var bannerText = ''
  if (running) {
    banner = 'running'
    bannerText = '1 个查询进行中，点击查看'
  } else if (!account) {
    banner = 'guest'
    bannerText = '未登录'
  } else if (tier === 'free') {
    banner = 'free'
    bannerText = '尚未开通会员'
  }
  return {
    loggedIn: !!account,
    tier: tier,
    banner: banner,
    bannerText: bannerText,
    runningId: running ? running.id : '',
    showAnnounce: !session.announceSeen,
    showDone: !!session.announceSeen && !!unseen,
    doneSingle: !!(unseen && unseen.items.length === 1),
    doneName: unseen && unseen.items.length === 1 ? unseen.items[0].name : '',
    doneLine: unseen && unseen.items.length === 1 ? (unseen.created + ' · ' + describeItem(unseen.items[0]).line.replace('命中 ', '已命中 ')) : '',
    doneText: unseen ? summaryText(unseen) + '，点击查看。' : '',
    doneQueryId: unseen ? unseen.id : '',
    examplesError: !!session.examplesError,
    examples: session.examplesError ? [] : homeExamples(),
    platformTotal: 26,
    draft: session.draft || ''
  }
}

function maskCode(code) {
  if (!code || code.length < 4) return ''
  return code.slice(0, 4) + '****'
}

function profile() {
  var account = acc()
  if (!account) {
    return { guest: true, tier: 'guest', title: '', badge: '', lines: [], bound: false, bindText: '兑换邀请码', bindSub: '' }
  }
  var tier = tierOf(account)
  var title = '免费用户 · 未开通会员'
  var badge = ''
  var lines = []
  var regularExpiry = [account.paidRegularExpiry, account.inviteExpiry].filter(function (until) {
    return date.active(until)
  }).sort().pop()
  if (tier === 'vip') {
    title = 'VIP 至 ' + account.paidVipExpiry
    badge = 'VIP'
    if (regularExpiry) lines.push('普通会员 至 ' + regularExpiry)
  } else if (tier === 'regular') {
    title = '普通会员 至 ' + regularExpiry
  }
  return {
    guest: false,
    tier: tier,
    title: title,
    badge: badge,
    lines: lines,
    bound: !!account.inviteBound,
    bindText: account.inviteBound ? ('邀请码 ' + maskCode(account.inviteCode)) : '兑换邀请码',
    bindSub: ''
  }
}

function memberCenter() {
  var account = acc()
  var tier = tierOf(account)
  var until = ''
  if (account && tier === 'vip') until = account.paidVipExpiry
  else if (account && tier === 'regular') {
    until = date.active(account.paidRegularExpiry) ? account.paidRegularExpiry : account.inviteExpiry
  }
  return {
    guest: !account,
    tier: tier,
    statusLine: require('../utils/member').statusLine(tier, until)
  }
}

function membershipPlans() {
  return require('../utils/member').plansFromProducts(PRODUCTS)
}

function queryView(query) {
  if (!query) return null
  if (query.status === 'queued') {
    return {
      id: query.id,
      queued: true,
      running: false,
      done: false,
      head: '',
      percent: 0,
      items: [],
      failCount: 0,
      showRetryAll: false,
      showFailBanner: false,
      allFail: false
    }
  }
  if (query.status === 'running') {
    var percent = query.total ? Math.round(query.progress / query.total * 100) : 0
    return {
      id: query.id,
      queued: false,
      running: true,
      done: false,
      head: '已完成 ' + query.progress + ' / ' + query.total + '',
      percent: percent,
      items: [],
      failCount: 0,
      showRetryAll: false,
      showFailBanner: false,
      allFail: false
    }
  }
  var items = query.items.map(describeItem)
  var hit = 0
  var miss = 0
  var fail = 0
  items.forEach(function (item) {
    if (item.failed) fail += 1
    else if (item.missed) miss += 1
    else hit += 1
  })
  var head = '共 ' + items.length + ' 条：命中 ' + hit + ' · 未命中 ' + miss + ' · 失败 ' + fail
  if (fail === 0 && hit === 0) head = '共 ' + items.length + ' 条，均未命中。'
  var allFail = fail > 0 && fail === items.length
  return {
    id: query.id,
    queued: false,
    running: false,
    done: true,
    head: head,
    percent: 100,
    items: items,
    failCount: fail,
    showRetryAll: fail > 1,
    showFailBanner: fail > 0,
    allFail: allFail,
    failBanner: allFail ? '全部查询失败，失败不扣次数。' : '部分查询失败，失败不扣次数。',
    itemRetry: !allFail
  }
}

function describeItem(item) {
  if (item.error) {
    return {
      name: item.name,
      badge: '失败',
      badgeCls: 'bad',
      line: '索引暂时不可用。',
      failed: true,
      missed: false,
      hit: false
    }
  }
  var found = searchRaw(item.name)
  if (found.outcome === 'miss') {
    return {
      name: item.name,
      badge: '未命中',
      badgeCls: 'muted',
      line: '当前索引未收录该节目名。',
      failed: false,
      missed: true,
      hit: false
    }
  }
  var count = found.locked_window && found.locked_window.count ? found.locked_window.count : 0
  if (!count) {
    found.records.forEach(function (rec) {
      if (!rec.locked) count += 1
    })
  }
  var line = '命中 ' + count + ' 条 · 涉及 ' + found.platform_count + ' 个平台'
  if (found.outcome === 'recent_only') line = '有最新记录，可委托取证'
  return {
    name: item.name,
    badge: '已命中',
    badgeCls: 'ok',
    line: line,
    evidencePlatform: found.records.filter(function (rec) { return !rec.locked && rec.platform }).map(function (rec) { return rec.platform }).filter(function (name, i, all) { return all.indexOf(name) === i }).join('、'),
    failed: false,
    missed: false,
    hit: true
  }
}

function findQuery(id) {
  var account = acc()
  if (!account) return null
  var found = null
  account.queries.forEach(function (query) {
    if (query.id === id) found = query
  })
  return found
}

function nextNo(prefix) {
  var session = state.load()
  session.seq += 1
  var seq = String(session.seq)
  while (seq.length < 3) seq = '0' + seq
  return prefix + date.today().replace(/-/g, '') + seq
}

function reusable(status) {
  return status === 'pending' || status === 'failed' ||
    status === 'paying' || status === 'confirming' || status === 'granting' || status === 'grant_error'
}

function findOrder(no) {
  var account = acc()
  if (!account) return null
  var found = null
  account.orders.forEach(function (item) {
    if (item.no === no) found = item
  })
  return found
}

function orderTitle(item) {
  var product = PRODUCTS[item.product] || { title: '订单' }
  if (item.programName && String(item.product).indexOf('single_program') === 0) {
    return '单节目解锁 · ' + item.programName
  }
  return product.title
}

function viewOrder(item) {
  if (!item) return null
  var product = PRODUCTS[item.product] || { card: '订单', price: 0, blurb: '', days: 0 }
  var account = acc()
  var renewLine = ''
  if (item.product === 'membership_vip' && account && date.active(account.paidVipExpiry) && item.status !== 'granted') {
    renewLine = '到期日将从 ' + account.paidVipExpiry + ' 延至 ' + date.addDays(account.paidVipExpiry, 30)
  }
  var successLine = item.resultText || ''
  return {
    orderNo: item.no,
    product: item.product,
    programName: item.programName || '',
    price: product.price,
    priceText: '¥' + product.price,
    card: product.card,
    title: orderTitle(item),
    blurb: product.blurb,
    renewLine: renewLine,
    coverLine: '',
    status: item.status,
    statusText: STATUS_TEXT[item.status] || '',
    day: item.day,
    line: (STATUS_TEXT[item.status] || '') + ' · ¥' + product.price + ' · ' + item.day,
    allowRepay: !!item.unpaidConfirmed && (item.status === 'failed' || item.status === 'cancelled' || item.status === 'pending'),
    allowCheck: item.status === 'failed' && !item.unpaidConfirmed,
    showPay: item.status === 'pending',
    successLine: successLine,
    payText: '立即支付 ¥' + product.price
  }
}

function applyGrant(item) {
  var account = acc()
  if (!account || item.grantedOnce) return
  var product = PRODUCTS[item.product]
  var today = date.today()
  if (item.product === 'membership_vip') {
    var vipStart = date.active(account.paidVipExpiry) ? account.paidVipExpiry : today
    account.paidVipExpiry = date.addDays(vipStart, product.days)
    item.resultText = 'VIP 已发放，有效至 ' + account.paidVipExpiry
  } else if (item.product === 'membership_regular') {
    var regularStart = date.active(account.paidRegularExpiry) ? account.paidRegularExpiry : today
    account.paidRegularExpiry = date.addDays(regularStart, product.days)
    item.resultText = '普通会员已发放，有效至 ' + account.paidRegularExpiry
  } else if (item.product === 'single_program_30d' || item.product === 'single_program_90d') {
    var tier = item.product === 'single_program_30d' ? 'program_30' : 'program_90'
    var existing = null
    account.programs.forEach(function (row) {
      if (row.name === item.programName && row.tier === tier) existing = row
    })
    var start = existing && date.active(existing.expiry) ? existing.expiry : today
    var expiry = date.addDays(start, product.days)
    if (existing) existing.expiry = expiry
    else account.programs.push({ name: item.programName, tier: tier, expiry: expiry })
    item.resultText = '本节目已解锁，有效至 ' + expiry
  }
  item.grantedOnce = true
}

function evidenceSteps(status) {
  if (status === 'failed') {
    return [
      { name: '已提交', cls: 'done' },
      { name: '已受理', cls: 'done' },
      { name: '取证失败', cls: 'bad' }
    ]
  }
  if (status === 'closed') {
    return [
      { name: '已提交', cls: 'done' },
      { name: '取证失败', cls: 'done' },
      { name: '已关闭', cls: 'bad' }
    ]
  }
  if (status === 'cancelled') {
    return [
      { name: '已提交', cls: 'done' },
      { name: '已取消', cls: 'bad' }
    ]
  }
  var flow = [
    ['submitted', '已提交'],
    ['accepted', '已受理'],
    ['pay', '待付款（线下）'],
    ['working', '取证中'],
    ['delivered', '已交付'],
    ['done', '已完成']
  ]
  var index = 0
  flow.forEach(function (pair, i) {
    if (pair[0] === status) index = i
  })
  return flow.map(function (pair, i) {
    return { name: pair[1], cls: i < index ? 'done' : (i === index ? 'now' : 'wait') }
  })
}

function viewEvidence(item) {
  var badgeCls = 'muted'
  if (item.status === 'failed' || item.status === 'closed' || item.status === 'cancelled') badgeCls = 'bad'
  else if (item.status === 'done' || item.status === 'delivered') badgeCls = 'ok'
  else if (item.status === 'pay') badgeCls = 'warn'
  else badgeCls = 'now'
  return {
    id: item.id,
    no: item.no,
    name: item.name,
    kindText: item.kind === 'recent' ? '近期取证' : '常规取证',
    badge: EVIDENCE_BADGE[item.status] || '',
    badgeCls: badgeCls,
    day: item.day,
    sub: '申请编号 ' + item.no + ' · ' + item.day + ' 提交',
    steps: evidenceSteps(item.status),
    message: EVIDENCE_TEXT[item.status] || '',
    reason: item.reason || '',
    showPayBanner: item.status === 'pay',
    payBanner: '请按客服指引完成线下付款，付款不在小程序内完成。',
    hasRefund: item.refundAmount > 0,
    refundAmount: item.refundAmount || 0,
    refundMethod: item.refundMethod || '',
    refundTime: item.refundTime || '',
    canWithdraw: item.status === 'submitted',
    canConfirm: item.status === 'delivered',
    contactFrom: 'evidence:' + item.no,
    phoneText: maskPhone(item.phone) || '—',
    quoteText: '—',
    reasonText: item.reason || '—',
    platformText: item.platform || '—',
    noteText: item.note || '—',
    refundText: item.refundAmount > 0 ? ('¥' + item.refundAmount) : '—',
    times: [
      { label: '提交', value: item.day || '—' },
      { label: '受理', value: '—' },
      { label: '报价', value: '—' },
      { label: '付款', value: '—' },
      { label: '交付', value: '—' },
      { label: '完成', value: '—' },
      { label: '失败', value: '—' },
      { label: '关闭', value: '—' },
      { label: '取消', value: '—' }
    ]
  }
}

function maskPhone(phone) {
  var value = String(phone || '')
  if (value.length < 7) return value
  return value.slice(0, 3) + '****' + value.slice(-4)
}

module.exports = {
  bootstrap: function () { state.load() },
  reset: function () { state.reset() },
  splashSeen: function () { return require('./splash').seen() },
  markSplashSeen: function () { require('./splash').markSeen() },
  markAnnounceSeen: function () {
    state.load().announceSeen = true
    save()
  },
  homeState: homeState,
  toggleExamples: function () {
    var session = state.load()
    session.examplesError = !session.examplesError
    save()
    return homeState()
  },
  login: function () {
    var session = state.load()
    if (session.current === 'guest') session.current = 'free'
    save()
    return profile()
  },
  switchAccount: function (id) {
    var session = state.load()
    if (require('../config').mode !== 'mock') return profile()
    state.clearSession()
    if (id !== 'guest' && id !== 'free' && id !== 'member') return profile()
    session.current = id
    save()
    return profile()
  },
  accountKind: function () { return state.currentId() },
  profile: profile,
  memberCenter: memberCenter,
  membershipPlans: membershipPlans,
  setDraft: function (value) {
    state.load().draft = String(value || '')
    save()
  },
  getDraft: function () { return state.load().draft || '' },
  parseTitles: function (raw) { return text.parseTitles(raw) },
  parseFile: function (name, size) {
    var lower = String(name || '').toLowerCase()
    if (!lower) return { ok: false, code: 'read', message: '文件读取失败，请稍后重试。' }
    if (lower.slice(-5) !== '.xlsx') return { ok: false, code: 'xlsx', message: '仅支持 .xlsx，请另存后再上传。' }
    if (!size) return { ok: false, code: 'empty', message: '文件为空，请重新选择。' }
    if (size > 5 * 1024 * 1024) return { ok: false, code: 'size', message: '文件超过 5MB，请拆分后再上传。' }
    if (lower.indexOf('nocol') >= 0) return { ok: false, code: 'col', message: '未找到「节目名」列，请检查表头后重试。' }
    if (lower.indexOf('manyrows') >= 0) return { ok: false, code: 'rows', message: '超过 500 行，请删减后再上传。' }
    return { ok: false, code: 'read', message: '文件读取失败，请稍后重试。' }
  },
  createQuery: function (names) {
    var account = acc()
    if (!account) return { ok: false, message: '请先登录' }
    if (!names || !names.length) return { ok: false, message: '请先输入节目名' }
    var session = state.load()
    session.seq += 1
    var query = {
      id: 'q' + session.seq,
      created: date.today(),
      status: 'queued',
      seen: false,
      progress: 0,
      total: names.length,
      items: names.map(function (name) {
        return { name: name, error: name === '南岭追光' }
      })
    }
    account.queries.unshift(query)
    save()
    return { ok: true, id: query.id, query: queryView(query) }
  },
  getQuery: function (id) {
    return queryView(findQuery(id))
  },
  advanceQuery: function (id) {
    var query = findQuery(id)
    if (!query) return null
    if (query.status === 'queued') {
      query.status = 'running'
      query.progress = 0
      save()
    } else if (query.status === 'running') {
      query.progress += 1
      if (query.progress >= query.total) {
        query.progress = query.total
        query.status = 'done'
      }
      save()
    }
    return queryView(query)
  },
  markQuerySeen: function (id) {
    var query = findQuery(id)
    if (!query) return
    query.seen = true
    save()
  },
  retryItem: function (id, name) {
    var query = findQuery(id)
    if (!query) return null
    query.items.forEach(function (item) {
      if (item.name === name) item.error = false
    })
    save()
    return queryView(query)
  },
  retryAll: function (id) {
    var query = findQuery(id)
    if (!query) return null
    query.items.forEach(function (item) { item.error = false })
    save()
    return queryView(query)
  },
  listRecords: function () {
    var account = acc()
    if (!account) return []
    var rows = []
    account.queries.forEach(function (query) {
      query.items.forEach(function (item) {
        var view = describeItem(item)
        var tail = view.failed ? '查询失败' : (view.missed ? '未命中' : view.line.replace('命中 ', '已命中 '))
        rows.push({
          id: query.id,
          name: view.name,
          badge: view.badge,
          badgeCls: view.badgeCls,
          line: query.created + ' · ' + tail
        })
      })
    })
    return rows
  },
  searchProgram: function (name, options) { return searchRaw(name, options || null) },
  exampleReport: function (name) {
    var allowed = false
    catalog.examples.forEach(function (title) {
      if (text.foldKey(title) === text.foldKey(name)) allowed = true
    })
    if (!allowed) return null
    var summary = exampleSummary(name)
    var facts = require('../utils/present').exampleFacts(summary.report.records)
    summary.report.line = facts.line
    summary.report.sub = facts.sub
    return summary.report
  },
  programMeta: function (name) { return metaOf(name) },
  tierOfAccount: function () { return tierOf(acc()) },
  checkout: function (product, programName) {
    var account = acc()
    if (!account) return { ok: false, message: '请先登录' }
    if (!PRODUCTS[product]) return { ok: false, message: '商品不存在' }
    if ((product === 'single_program_30d' || product === 'single_program_90d' || product === 'screenshot') && date.active(account.paidVipExpiry)) {
      return { ok: false, code: 'covered', message: '你的 VIP 已覆盖，无需再买本节目档位。' }
    }
    var existing = null
    account.orders.forEach(function (item) {
      if (item.product === product && (item.programName || '') === (programName || '') && reusable(item.status)) {
        existing = item
      }
    })
    if (!existing) {
      existing = {
        no: nextNo('DD'),
        product: product,
        price: PRODUCTS[product].price,
        status: 'pending',
        day: date.today(),
        programName: programName || '',
        unpaidConfirmed: true,
        grantedOnce: false,
        resultText: ''
      }
      account.orders.unshift(existing)
    }
    save()
    return { ok: true, order: viewOrder(existing) }
  },
  getOrder: function (no) { return viewOrder(findOrder(no)) },
  listOrders: function () {
    var account = acc()
    if (!account) return []
    return account.orders.map(function (item) {
      var view = viewOrder(item)
      return { orderNo: view.orderNo, title: view.title, line: view.line, status: view.status }
    })
  },
  startPay: function (no) {
    var item = findOrder(no)
    if (!item) return { ok: false, message: '订单不存在' }
    if (item.status === 'granted' || item.status === 'grant_error' || item.status === 'confirming' || item.status === 'granting' || item.status === 'paying') {
      return { ok: false, message: '请勿重复支付', order: viewOrder(item) }
    }
    if ((item.status === 'failed' || item.status === 'cancelled') && !item.unpaidConfirmed) {
      return { ok: false, message: '请先查看订单状态', order: viewOrder(item) }
    }
    item.status = 'paying'
    item.unpaidConfirmed = false
    save()
    return { ok: true, order: viewOrder(item) }
  },
  setOrderStatus: function (no, status) {
    var item = findOrder(no)
    if (!item || item.status === 'granted') return viewOrder(item)
    item.status = status
    save()
    return viewOrder(item)
  },
  settle: function (no, outcome) {
    var item = findOrder(no)
    if (!item) return { ok: false, message: '订单不存在' }
    if (item.grantedOnce || item.status === 'granted') {
      return { ok: false, message: '订单已支付', order: viewOrder(item) }
    }
    if (outcome === 'success') {
      item.status = 'granted'
      applyGrant(item)
      item.unpaidConfirmed = false
    } else if (outcome === 'fail') {
      item.status = 'failed'
      item.unpaidConfirmed = false
    } else if (outcome === 'cancel') {
      item.status = 'cancelled'
      item.unpaidConfirmed = true
    } else if (outcome === 'timeout') {
      item.status = 'confirming'
      item.unpaidConfirmed = false
    } else if (outcome === 'grant_error') {
      item.status = 'grant_error'
      item.unpaidConfirmed = false
    }
    save()
    return { ok: true, order: viewOrder(item) }
  },
  checkStatus: function (no) {
    var item = findOrder(no)
    if (!item) return null
    if (item.status === 'failed' || item.status === 'cancelled' || item.status === 'pending') {
      item.unpaidConfirmed = true
      save()
    }
    return viewOrder(item)
  },
  repay: function (no) {
    var item = findOrder(no)
    if (!item) return { ok: false, message: '订单不存在' }
    if (!item.unpaidConfirmed) return { ok: false, message: '请先查看订单状态', order: viewOrder(item) }
    if (item.status !== 'failed' && item.status !== 'cancelled' && item.status !== 'pending') {
      return { ok: false, message: '订单已支付，不能再次支付', order: viewOrder(item) }
    }
    var kept = item.no
    item.status = 'paying'
    item.unpaidConfirmed = false
    save()
    var view = viewOrder(item)
    view.sameNo = view.orderNo === kept
    return { ok: true, order: view }
  },
  inviteState: function () {
    var account = acc()
    if (!account) return { guest: true, bound: false }
    return { guest: false, bound: !!account.inviteBound }
  },
  redeem: function (raw) {
    var account = acc()
    if (!account) return { ok: false, code: 'login', message: '请先登录' }
    if (account.inviteBound) {
      return { ok: false, code: 'bound', message: '已绑定过邀请码，不能再填' }
    }
    if (account.missLockUntil && Date.now() < account.missLockUntil) {
      return { ok: false, code: 'limited', message: '操作太频繁，请 1 小时后再试。' }
    }
    var code = text.normalizeCode(raw)
    var fail = function (kind, message) {
      account.misses = (account.misses || 0) + 1
      if (account.misses >= 5) account.missLockUntil = Date.now() + 3600 * 1000
      save()
      return { ok: false, code: kind, message: message }
    }
    if (!text.codeWellFormed(code)) return fail('invalid', '邀请码无效，请核对后再试。')
    var kind = CODES[code] || 'invalid'
    if (kind === 'expired') return fail('expired', '邀请码已过期，不能再兑换。')
    if (kind === 'used') return fail('used', '邀请码已被使用。')
    if (kind !== 'ok') return fail('invalid', '邀请码无效，请核对后再试。')
    var hasVip = date.active(account.paidVipExpiry)
    var vipBefore = account.paidVipExpiry
    account.inviteBound = true
    account.inviteCode = code
    account.inviteExpiry = date.addDays(date.today(), 30)
    account.misses = 0
    save()
    return {
      ok: true,
      code: 'success',
      expiry: account.inviteExpiry,
      hasVip: hasVip,
      headline: hasVip ? ('已发放普通会员 30 天，有效至 ' + account.inviteExpiry) : '已发放普通会员 30 天',
      sub: hasVip ? '' : ('有效至 ' + account.inviteExpiry),
      vipUnchanged: account.paidVipExpiry === vipBefore
    }
  },
  evidenceSampleName: function () { return '雾港来信' },
  listEvidence: function () {
    var account = acc()
    if (!account) return []
    return account.evidence.map(function (item) {
      var view = viewEvidence(item)
      return {
        id: view.id,
        name: view.name,
        platform: item.platform || '',
        badge: view.badge,
        badgeCls: view.badgeCls,
        line: view.hasRefund ? '已全额退款 ¥' + view.refundAmount : view.day
      }
    })
  },
  evidenceDetail: function (id) {
    var account = acc()
    if (!account) return null
    var found = null
    account.evidence.forEach(function (item) {
      if (item.id === id) found = item
    })
    return found ? viewEvidence(found) : null
  },
  createEvidence: function (form) {
    var account = acc()
    if (!account) return { ok: false, message: '请先登录' }
    var name = text.displayOf(form.name || '')
    var contact = text.displayOf(form.contact || '') || '用户'
    var phone = String(form.phone || '').replace(/\s/g, '')
    if (!name) return { ok: false, message: '请填写节目名' }
    if (!/^1\d{10}$/.test(phone)) return { ok: false, message: '请填写正确的手机号' }
    var openId = ''
    account.evidence.forEach(function (item) {
      if (item.name === name && (item.status === 'submitted' || item.status === 'accepted' || item.status === 'pay' || item.status === 'working')) {
        openId = item.id
      }
    })
    if (openId) return { ok: false, code: 'duplicate', id: openId, message: '已有申请在处理中' }
    var session = state.load()
    session.evidenceSeq += 1
    var item = {
      id: 'n' + session.evidenceSeq,
      no: 'QZ' + date.today().replace(/-/g, '') + date.pad2(session.evidenceSeq),
      name: name,
      kind: form.kind === 'recent' ? 'recent' : 'normal',
      status: 'submitted',
      day: date.today(),
      contact: contact,
      phone: phone,
      note: String(form.note || '').slice(0, 500),
      platform: form.kind === 'recent' ? '' : text.displayOf(form.platform || ''),
      org: text.displayOf(form.org || ''),
      reason: '',
      refundAmount: 0,
      refundMethod: '',
      refundTime: ''
    }
    account.evidence.unshift(item)
    save()
    return { ok: true, id: item.id, no: item.no, phoneMask: maskPhone(phone) }
  },
  withdrawEvidence: function (id) {
    var account = acc()
    if (!account) return { ok: false, message: '请先登录' }
    var found = null
    account.evidence.forEach(function (item) {
      if (item.id === id) found = item
    })
    if (!found) return { ok: false, message: '申请不存在' }
    if (found.status !== 'submitted') return { ok: false, message: '当前状态不能撤回' }
    found.status = 'cancelled'
    save()
    return { ok: true, detail: viewEvidence(found) }
  },
  confirmEvidence: function (id) {
    var account = acc()
    if (!account) return { ok: false, message: '请先登录' }
    var found = null
    account.evidence.forEach(function (item) {
      if (item.id === id) found = item
    })
    if (!found) return { ok: false, message: '申请不存在' }
    if (found.status !== 'delivered') return { ok: false, message: '当前状态不能确认' }
    found.status = 'done'
    save()
    return { ok: true, detail: viewEvidence(found) }
  },
  agreements: function () {
    return [
      { id: 'user', title: '用户协议' },
      { id: 'privacy', title: '隐私指引' },
      { id: 'member', title: '会员服务协议' }
    ]
  },
  agreement: function (id) {
    var docs = {
      user: {
        title: '用户协议',
        paragraphs: [
          '运营主体：那豆在架',
          '本协议适用于你使用片单检索、报告查看、会员与委托取证相关功能。',
          '检索结果来自已收录平台的片单记录，展示范围随你的权益变化。',
          '请勿利用本服务从事违法活动。账号仅供本人使用。'
        ]
      },
      privacy: {
        title: '用户隐私保护指引',
        paragraphs: [
          '运营主体：那豆在架',
          '为完成委托取证的联系，我们会在你主动填写时收集联系人、手机号和单位名称。',
          '手机号仅用于客服与你确认取证进度，不会用于广告。',
          '正式版上线前，须在微信公众平台完成本指引的配置，才会向你申请相应授权。',
          '你可以在个人中心查阅本指引。除本页外，其他页面不展示运营主体名称。'
        ]
      },
      member: {
        title: '会员服务协议',
        paragraphs: [
          '运营主体：那豆在架',
          '会员、单节目解锁和单张截图均为虚拟权益，支付成功后按约定时长生效。',
          '邀请码权益与付费会员各自计时，天数不相加，同时有效时按其中较高档查看。',
          '苹果手机上的退款需通过苹果应用商店申请。安卓退款由客服按订单核实后处理。',
          '权益到期后，超出免费范围的记录将重新锁定。'
        ]
      }
    }
    return docs[id] || null
  },
  exportBook: function (name) {
    var account = acc()
    if (!account) return { ok: false, message: '请先登录' }
    var meta = metaOf(name)
    var g = grantsOf(account, meta.name)
    if (!g.vip && !g.regular) return { ok: false, message: '当前权益不能导出' }
    var found = searchRaw(meta.name)
    var rows = []
    found.records.forEach(function (rec) {
      if (rec.locked || !rec.can_download_report) return
      rows.push({
        platform: rec.platform || '其他平台',
        subtype: rec.platform_subtype || '',
        category: rec.category || '',
        type: '',
        uploaded: meta.name,
        matched: found.program_name,
        date: rec.checked_at ? String(rec.checked_at).slice(0, 10) : ''
      })
    })
    if (!rows.length) return { ok: false, message: '没有可导出的记录' }
    return {
      ok: true,
      count: rows.length,
      filename: xlsx.filename(rows.length),
      bytes: xlsx.build({
        title: '那豆在架 命中结果',
        scope: '节目 ' + meta.name + ' · 已收录平台',
        day: date.today(),
        rows: rows
      })
    }
  },
  maskPhone: maskPhone
}

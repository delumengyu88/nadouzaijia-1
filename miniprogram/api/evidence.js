// User evidence responses from PR #30 head 5d7e678.
// Phone is required on that head: empty -> phone_required, bad format -> invalid_phone.
// Status badges stay on the English `status` and this map, not `status_text`.
var BADGE = {
  submitted: '已提交',
  accepted: '已受理',
  pay: '待付款（线下）',
  working: '取证中',
  delivered: '已交付',
  done: '已完成',
  failed: '取证失败',
  closed: '已关闭',
  cancelled: '已取消'
}

var TEXT = {
  pay: '请按客服指引完成线下付款，付款不在小程序内完成。',
  delivered: '材料已线下交付，请确认查收。'
}

function dash(value) {
  if (value === undefined || value === null) return '—'
  var text = String(value).trim()
  return text ? text : '—'
}

function shownTime(value) {
  if (value === undefined || value === null) return '—'
  var text = String(value).trim()
  if (!text) return '—'
  var match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(text)
  if (match) return match[1] + ' ' + match[2]
  return text
}

function shownPhone(value) {
  var text = dash(value)
  if (text === '—') return text
  if (/^1\d{10}$/.test(text)) return text.slice(0, 3) + '****' + text.slice(-4)
  return text
}

function refundLine(item) {
  var amount = item.refund_amount
  var hasAmount = amount !== undefined && amount !== null && amount !== '' && Number(amount) > 0
  var parts = []
  if (hasAmount) parts.push('¥' + Number(amount))
  if (item.refund_method) parts.push(String(item.refund_method))
  if (item.refund_note) parts.push(String(item.refund_note))
  return parts.length ? parts.join(' · ') : '—'
}

function money(value) {
  if (value === undefined || value === null || value === '') return '—'
  var amount = Number(value)
  if (amount !== amount) return '—'
  return '¥' + amount
}

function platformText(value) {
  if (value === undefined || value === null) return ''
  return String(value).trim()
}

function badgeCls(status) {
  if (status === 'failed' || status === 'closed' || status === 'cancelled') return 'bad'
  if (status === 'done' || status === 'delivered') return 'ok'
  if (status === 'pay') return 'warn'
  if (BADGE[status]) return 'now'
  return 'muted'
}

function steps(status) {
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

function presentList(item) {
  item = item || {}
  var status = String(item.status || '')
  return {
    id: item.id,
    no: item.no || '',
    name: dash(item.name),
    platform: platformText(item.platform),
    kind: item.kind || '',
    badge: BADGE[status] || '—',
    badgeCls: badgeCls(status),
    line: shownTime(item.created_at)
  }
}

function presentDetail(item) {
  item = item || {}
  var status = String(item.status || '')
  var reason = dash(item.failure_reason)
  var refundAmount = item.refund_amount
  var hasRefund = refundAmount !== undefined && refundAmount !== null && refundAmount !== '' && Number(refundAmount) > 0
  var phone = shownPhone(item.phone)
  return {
    id: item.id,
    no: item.no || '',
    name: dash(item.name),
    kindText: item.kind === 'recent' ? '近期取证' : '常规取证',
    platformText: platformText(item.platform) || '—',
    badge: BADGE[status] || '—',
    badgeCls: badgeCls(status),
    sub: '申请编号 ' + dash(item.no),
    steps: steps(status),
    message: TEXT[status] || '',
    reason: reason === '—' ? '' : reason,
    reasonText: reason,
    phoneText: phone,
    quoteText: money(item.quote_amount),
    noteText: dash(item.note),
    showPayBanner: status === 'pay',
    payBanner: TEXT.pay,
    hasRefund: hasRefund,
    refundAmount: hasRefund ? Number(refundAmount) : 0,
    refundMethod: item.refund_method || '',
    refundTime: '',
    refundText: refundLine(item),
    canWithdraw: status === 'submitted',
    canConfirm: status === 'delivered',
    contactFrom: 'evidence:' + (item.no || ''),
    times: [
      { label: '提交', value: shownTime(item.created_at) },
      { label: '受理', value: shownTime(item.accepted_at) },
      { label: '报价', value: shownTime(item.quoted_at) },
      { label: '付款', value: shownTime(item.payment_confirmed_at) },
      { label: '交付', value: shownTime(item.delivered_at) },
      { label: '完成', value: shownTime(item.completed_at) },
      { label: '失败', value: shownTime(item.failed_at) },
      { label: '关闭', value: shownTime(item.closed_at) },
      { label: '取消', value: shownTime(item.cancelled_at) }
    ]
  }
}

function trimEnds(value) {
  return String(value || '').replace(/^[\s\u3000]+|[\s\u3000]+$/g, '')
}

function createPayload(form) {
  form = form || {}
  var kind = form.kind === 'recent' ? 'recent' : 'normal'
  var payload = {
    name: trimEnds(form.name),
    kind: kind,
    phone: String(form.phone || '').replace(/[\s\u3000]/g, '')
  }
  var note = trimEnds(form.note)
  if (note) payload.note = note.slice(0, 50)
  if (kind !== 'recent') {
    var platform = trimEnds(form.platform)
    if (platform) payload.platform = platform
  }
  return payload
}

function createOutcome(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, code: 'network', message: '网络异常，请重试' }
  }
  if (body.code === 'duplicate') return { ok: false, code: 'duplicate', id: body.id, no: body.no }
  if (body.code === 'phone_required') return { ok: false, code: 'phone_required' }
  if (body.code === 'invalid_phone') return { ok: false, code: 'invalid_phone' }
  if (body.code === 'no_unavailable') return { ok: false, code: 'no_unavailable' }
  if (body.code) return { ok: false, code: String(body.code), message: '网络异常，请重试' }
  if (body.id && body.no) return { ok: true, id: body.id, no: body.no }
  return { ok: false, code: 'network', message: '网络异常，请重试' }
}

function actionOutcome(body) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, code: 'network', message: '网络异常，请重试' }
  }
  if (body.code === 'invalid_state') return { ok: false, code: 'invalid_state' }
  if (body.code) return { ok: false, code: String(body.code), message: '网络异常，请重试' }
  if (body.id) return { ok: true, detail: presentDetail(body) }
  return { ok: false, code: 'network', message: '网络异常，请重试' }
}

function listRows(body) {
  if (!Array.isArray(body)) return null
  return body
}

function postAction(request, id, action) {
  return request('/api/v1/evidence-requests/' + encodeURIComponent(id || '') + '/' + action, {}, {
    method: 'POST',
    readBody: true
  }).then(function (body) {
    return actionOutcome(body)
  }, function () {
    return { ok: false, code: 'network', message: '网络异常，请重试' }
  })
}

module.exports = {
  badge: BADGE,
  presentList: presentList,
  presentDetail: presentDetail,
  createPayload: createPayload,
  createOutcome: createOutcome,
  actionOutcome: actionOutcome,
  listRows: listRows,
  postAction: postAction
}

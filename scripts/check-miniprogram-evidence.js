var assert = require('assert')
var fs = require('fs')
var path = require('path')

var root = path.join(__dirname, '..', 'miniprogram')
var config = require(path.join(root, 'config'))
config.mode = 'live'
config.calibrating = false
config.trialMode = false
config.apiBase = '/test-api'

var calls = []
var toasts = []
var modals = []
var navigated = []
var queue = []

global.wx = {
  getStorageSync: function () { return null },
  setStorageSync: function () {},
  removeStorageSync: function () {},
  getSystemInfoSync: function () { return { statusBarHeight: 20, windowWidth: 375, windowHeight: 812, safeArea: { bottom: 812 } } },
  getMenuButtonBoundingClientRect: function () { return { top: 24, bottom: 56, height: 32, width: 87, left: 280, right: 367 } },
  showToast: function (opt) { toasts.push(opt || {}) },
  showModal: function (opt) { modals.push(opt || {}) },
  navigateTo: function (opt) { navigated.push(opt && opt.url) },
  navigateBack: function () {},
  switchTab: function () {},
  request: function (opt) {
    calls.push(opt)
    var next = queue.shift()
    if (!next) {
      if (opt.fail) opt.fail()
      return
    }
    next(opt)
  }
}
global.getCurrentPages = function () { return [{}, {}] }

function drain() {
  return new Promise(function (resolve) { setImmediate(resolve) })
}
function answer(statusCode, data) {
  queue.push(function (opt) { opt.success({ statusCode: statusCode, data: data }) })
}
function failNetwork() {
  queue.push(function (opt) { opt.fail() })
}

var state = require(path.join(root, 'api/state'))
state.setSession({
  token: 'evidence-token',
  expires_at: new Date(Date.now() + 86400000).toISOString(),
  user: { id: 'evidence-user', tier: 'free' }
})
var api = require(path.join(root, 'api'))
var evidence = require(path.join(root, 'api/evidence'))

var defs = {}
var currentRel = ''
global.Page = function (def) { defs[currentRel] = def }
;['pages/evidence/index', 'pages/evidence-list/index', 'pages/evidence-detail/index'].forEach(function (rel) {
  currentRel = rel
  require(path.join(root, rel))
})

function instantiate(rel) {
  var def = defs[rel]
  var page = { data: Object.assign({}, def.data || {}), query: {} }
  Object.keys(def).forEach(function (key) {
    if (typeof def[key] === 'function') page[key] = def[key].bind(page)
  })
  page.setData = function (obj) {
    Object.keys(obj || {}).forEach(function (key) { page.data[key] = obj[key] })
  }
  return page
}
function open(rel, query) {
  var page = instantiate(rel)
  if (page.onLoad) page.onLoad(query || {})
  return page
}
function read(rel) {
  return fs.readFileSync(path.join(root, rel), 'utf8')
}

var STATUSES = ['submitted', 'accepted', 'pay', 'working', 'delivered', 'done', 'failed', 'closed', 'cancelled']

function detailBody(status, extra) {
  return Object.assign({
    id: 'ev-' + status,
    no: 'QZ-' + status,
    name: '测试片',
    platform: null,
    kind: 'normal',
    status: status,
    status_text: '中文原文',
    created_at: null,
    note: null,
    phone: null,
    quote_amount: null,
    failure_reason: null,
    refund_method: null,
    refund_amount: null,
    refund_note: null,
    accepted_at: null,
    quoted_at: null,
    payment_confirmed_at: null,
    delivered_at: null,
    completed_at: null,
    failed_at: null,
    cancelled_at: null,
    closed_at: null
  }, extra || {})
}

async function main() {
  assert.strictEqual(api.isLive, true)
  assert.strictEqual(Object.keys(evidence.badge).length, 9)
  STATUSES.forEach(function (status) {
    var view = evidence.presentDetail(detailBody(status))
    assert.strictEqual(view.badge, evidence.badge[status], status)
    assert.notStrictEqual(view.badge, '中文原文')
    assert.strictEqual(view.canWithdraw, status === 'submitted', status + ' withdraw')
    assert.strictEqual(view.canConfirm, status === 'delivered', status + ' confirm')
    assert.strictEqual(view.phoneText, '—')
    assert.strictEqual(view.quoteText, '—')
    assert.strictEqual(view.reasonText, '—')
    assert.strictEqual(view.refundText, '—')
    assert.strictEqual(view.platformText, '—')
    view.times.forEach(function (row) { assert.strictEqual(row.value, '—', status + ' ' + row.label) })
  })
  var stamped = evidence.presentDetail(detailBody('working', {
    created_at: '2026-11-22T08:30:00+08:00',
    phone: '139****5678',
    quote_amount: 680,
    failure_reason: null,
    platform: '测试平台'
  }))
  assert.strictEqual(stamped.times[0].value, '2026-11-22 08:30')
  assert.strictEqual(evidence.presentDetail(detailBody('submitted', { created_at: '2026-11-22T04:00:00+00:00' })).times[0].value, '2026-11-22 12:00')
  assert.strictEqual(evidence.presentDetail(detailBody('submitted', { created_at: '2026-11-22T16:30:00Z' })).times[0].value, '2026-11-23 00:30')
  assert.strictEqual(evidence.presentList({ name: '片', status: 'submitted', created_at: '2026-11-22T20:15:00+00:00' }).line, '2026-11-23 04:15')
  assert.strictEqual(evidence.presentDetail(detailBody('submitted', { created_at: '2026-11-22T17:30:00+00:00' })).times[0].value, '2026-11-23 01:30')
  assert.strictEqual(evidence.presentDetail(detailBody('submitted', { created_at: null })).times[0].value, '—')
  assert.strictEqual(evidence.presentList({ name: '片', status: 'submitted', created_at: null }).line, '—')
  assert.strictEqual(stamped.phoneText, '139****5678')
  assert.strictEqual(stamped.quoteText, '¥680')
  assert.strictEqual(stamped.platformText, '测试平台')
  assert.strictEqual(evidence.presentList({ name: '有平台', platform: '测试平台', status: 'pay', created_at: null }).platform, '测试平台')
  assert.strictEqual(evidence.presentList({ name: '无平台', platform: null, status: 'done', created_at: null }).platform, '')
  var longNote = ''
  for (var noteIndex = 0; noteIndex < 60; noteIndex++) longNote += '记'
  var payload = evidence.createPayload({ name: ' 测试片 ', kind: 'normal', phone: '139 1234 5678', platform: '测试平台', note: longNote })
  assert.strictEqual(payload.phone, '13912345678')
  assert.strictEqual(payload.note.length, 50)
  assert.strictEqual(evidence.createPayload({ name: '近期', kind: 'recent', phone: '13912345678', platform: '不该出现' }).platform, undefined)
  assert.strictEqual(evidence.createOutcome({ code: 'duplicate', id: 'old', no: 'QZ-OLD' }).code, 'duplicate')
  assert.strictEqual(evidence.createOutcome({ id: 'new', no: 'QZ-NEW' }).ok, true)
  assert.strictEqual(evidence.createOutcome({ id: 'new-only' }).no, '')
  assert.strictEqual(evidence.createOutcome({ code: 'invalid_phone' }).code, 'invalid_phone')
  assert.strictEqual(evidence.createOutcome({ code: 'phone_required' }).code, 'phone_required')
  assert.strictEqual(evidence.actionOutcome({ code: 'invalid_state' }).code, 'invalid_state')

  var requestSrc = read('api/request.js')
  assert(requestSrc.indexOf('options.readBody') !== -1)
  assert(requestSrc.indexOf('options.exposeStatus') !== -1)
  var formWxml = read('pages/evidence/index.wxml')
  var listWxml = read('pages/evidence-list/index.wxml')
  var detailWxml = read('pages/evidence-detail/index.wxml')
  assert(formWxml.indexOf('手机号<text class="req">*</text>') !== -1)
  assert(formWxml.indexOf('focus="{{phoneFocus}}"') !== -1)
  assert(formWxml.indexOf("field {{phoneError ? 'field-bad' : ''}}") !== -1)
  var formWxss = read('pages/evidence/index.wxss')
  assert(formWxss.indexOf('.field.field-bad') !== -1)
  assert(formWxss.indexOf('#d64545') !== -1)
  assert.strictEqual(evidence.createOutcome({ code: 'no_unavailable', message: '提交失败，请稍后再试' }).code, 'no_unavailable')
  var blankPayload = evidence.createPayload({ name: ' 片 ', phone: ' \u3000 ', platform: ' \u3000 ', note: ' \u3000 ', kind: 'normal' })
  assert.strictEqual(blankPayload.phone, '')
  assert.strictEqual(blankPayload.platform, undefined)
  assert.strictEqual(blankPayload.note, undefined)
  assert(formWxml.indexOf('查看已有工单') !== -1)
  assert(formWxml.indexOf('知道了') !== -1)
  assert(formWxml.indexOf('bindtap="closeDuplicate"') !== -1)
  assert(formWxml.indexOf('客服线下交付，会尽快联系您。') !== -1)
  assert(formWxml.indexOf('1 个工作日') === -1)
  assert(formWxml.indexOf('（可选）') === -1)
  assert(formWxml.indexOf('补充说明（选填）') !== -1)
  assert(formWxml.indexOf('平台（选填）') !== -1)
  assert(formWxml.indexOf('placeholder="请输入节目名"') !== -1)
  assert(formWxml.indexOf('placeholder=""') !== -1)
  assert(formWxml.indexOf('placeholder="50 字以内"') !== -1)
  assert(formWxml.indexOf('placeholder="11 位手机号"') !== -1)
  assert(formWxml.indexOf('<picker') === -1, '类型不是 picker')
  assert(formWxml.indexOf('<view class="field type-field"><text>{{kindText}}</text><text class="chev">›</text></view>') !== -1)
  assert(formWxss.indexOf('padding-right: 32rpx') !== -1)
  assert(read('app.wxss').indexOf('.chev {\n  color: #c5cad3;\n  font-size: 36rpx;') !== -1)
  assert(formWxml.indexOf('<icon type="success" size="56" color="#16A34A"') !== -1)
  assert(formWxml.indexOf('客服会尽快联系您') !== -1)
  assert(formWxml.indexOf('wx:if="{{no}}"') !== -1)
  assert(formWxml.indexOf('class="done-primary" bindtap="open"') !== -1)
  assert(formWxml.indexOf('查看工单') !== -1)
  assert(formWxml.indexOf('class="done-link" bindtap="back"') !== -1)
  var formStart = formWxml.indexOf("phase == 'form'")
  var formEnd = formWxml.indexOf("phase == 'submitted'")
  var formBlock = formWxml.slice(formStart, formEnd)
  ;['类型', '节目名', '平台（选填）', '补充说明（选填）', '手机号'].forEach(function (label) {
    assert(formBlock.indexOf(label) !== -1, 'form keeps ' + label)
  })
  assert(formBlock.indexOf('showDuplicate') === -1, 'duplicate dialog does not replace form fields')
  assert(formBlock.indexOf("wx:if=\"{{networkError}}\"") !== -1)
  assert(formBlock.indexOf('网络异常，请重试') !== -1)
  assert(formWxss.indexOf('padding-top: 128rpx') !== -1)
  assert(formWxss.indexOf('margin-top: 32rpx') !== -1)
  assert(formWxss.indexOf('font-size: 40rpx') !== -1)
  assert(formWxss.indexOf('font-weight: 700') !== -1)
  assert(formWxss.indexOf('#1F2329') !== -1)
  assert(formWxss.indexOf('margin-top: 16rpx') !== -1)
  assert(formWxss.indexOf('font-size: 28rpx') !== -1)
  assert(formWxss.indexOf('#646A73') !== -1)
  assert(formWxss.indexOf('padding: 0 64rpx') !== -1)
  assert(formWxss.indexOf('width: 480rpx') !== -1)
  assert(formWxss.indexOf('height: 88rpx') !== -1)
  assert(formWxss.indexOf('margin: 48rpx 0 0') !== -1)
  assert(formWxss.indexOf('margin: 24rpx 0 0') !== -1)
  assert(formWxml.indexOf('这个节目已经提交过取证申请') !== -1)
  assert(formWxml.indexOf('网络异常，请重试') !== -1)
  assert(formWxml.indexOf('loading="{{submitting}}"') !== -1)
  assert(formWxml.indexOf('disabled="{{submitting}}"') !== -1)
  assert(listWxml.indexOf('还没有取证工单') !== -1)
  assert(listWxml.indexOf('wx:if="{{item.platform}}"') !== -1)
  assert(listWxml.indexOf('name-ellipsis') !== -1)
  assert(read('pages/evidence-list/index.wxss').indexOf('text-overflow: ellipsis') !== -1)
  assert(detailWxml.indexOf('wx:if="{{canWithdraw}}"') !== -1)
  assert(detailWxml.indexOf('wx:if="{{canConfirm}}"') !== -1)
  assert(detailWxml.indexOf('确认收到') !== -1)
  assert(detailWxml.indexOf('phoneText') !== -1)

  var blank = open('pages/evidence/index', { kind: 'recent' })
  assert.strictEqual(blank.data.name, '')
  assert.strictEqual(blank.data.note, '')
  assert.strictEqual(blank.data.phone, '')
  assert.strictEqual(JSON.stringify(blank.data).indexOf('雾港来信'), -1)

  function form() {
    var page = open('pages/evidence/index', { name: encodeURIComponent('测试片'), platform: encodeURIComponent('测试平台') })
    return page
  }

  var emptyPhone = form()
  var before = calls.length
  emptyPhone.submit()
  assert.strictEqual(calls.length, before, 'empty phone must not request')
  assert.strictEqual(emptyPhone.data.phoneError, '请填写手机号')
  assert.strictEqual(emptyPhone.data.phoneFocus, true)
  assert.strictEqual(toasts.length, 0)
  assert.strictEqual(emptyPhone.data.name, '测试片')
  assert.strictEqual(emptyPhone.data.platform, '测试平台')
  emptyPhone.onPhone({ detail: { value: '1' } })
  assert.strictEqual(emptyPhone.data.phoneError, '', 'editing the phone clears the error')
  assert.strictEqual(emptyPhone.data.phone, '1')

  var badPhone = form()
  badPhone.onNote({ detail: { value: '保留备注' } })
  badPhone.onPhone({ detail: { value: '139123456' } })
  before = calls.length
  var toastsBefore = toasts.length
  badPhone.submit()
  assert.strictEqual(calls.length, before, 'short phone must not request')
  assert.strictEqual(badPhone.data.phoneError, '请输入 11 位手机号')
  assert.strictEqual(badPhone.data.phoneFocus, true)
  assert.strictEqual(badPhone.data.phone, '139123456')
  assert.strictEqual(badPhone.data.note, '保留备注')
  assert.strictEqual(badPhone.data.name, '测试片')
  assert.strictEqual(toasts.length, toastsBefore)
  badPhone.onPhone({ detail: { value: '1391234567' } })
  assert.strictEqual(badPhone.data.phoneError, '', 'editing clears the format error')

  var invalid = form()
  invalid.onPhone({ detail: { value: '13912345678' } })
  invalid.onNote({ detail: { value: '保留备注' } })
  answer(422, { code: 'invalid_phone', message: '请填写正确的手机号' })
  invalid.submit()
  await drain()
  assert.strictEqual(calls[calls.length - 1].data.phone, '13912345678')
  assert.strictEqual(invalid.data.phoneError, '请输入 11 位手机号')
  assert.strictEqual(invalid.data.phoneFocus, true)
  assert.strictEqual(invalid.data.phone, '13912345678')
  assert.strictEqual(invalid.data.note, '保留备注')
  assert.strictEqual(invalid.data.phase, 'form')
  assert.strictEqual(toasts.length, toastsBefore)
  assert.strictEqual(invalid.data.submitting, false)
  assert.strictEqual(invalid.data.networkError, false)
  invalid.onPhone({ detail: { value: '13900001111' } })
  assert.strictEqual(invalid.data.phoneError, '')

  var required = form()
  required.onPhone({ detail: { value: '13912345678' } })
  answer(422, { code: 'phone_required', message: '请填写联系手机号' })
  required.submit()
  await drain()
  assert.strictEqual(required.data.phoneError, '请填写手机号')
  assert.strictEqual(required.data.phoneFocus, true)
  assert.strictEqual(toasts.length, toastsBefore)
  assert.strictEqual(required.data.name, '测试片')
  assert.strictEqual(required.data.networkError, false)

  var whitespace = form()
  whitespace.onPhone({ detail: { value: '  \u3000  ' } })
  before = calls.length
  whitespace.submit()
  assert.strictEqual(calls.length, before, 'whitespace phone must not request')
  assert.strictEqual(whitespace.data.phoneError, '请填写手机号')
  assert.strictEqual(whitespace.data.phoneFocus, true)
  assert.strictEqual(toasts.length, toastsBefore)
  var blankName = form()
  blankName.setData({ name: ' \u3000 ' })
  blankName.onPhone({ detail: { value: '13912345678' } })
  before = calls.length
  blankName.submit()
  assert.strictEqual(calls.length, before, 'whitespace name must not request')
  assert.strictEqual(blankName.data.nameError, '请填写节目名')
  assert.strictEqual(blankName.data.phone, '13912345678')

  var busy = form()
  busy.onPhone({ detail: { value: '13912345678' } })
  busy.onNote({ detail: { value: '保留备注' } })
  answer(503, { code: 'no_unavailable', message: '提交失败，请稍后再试' })
  busy.submit()
  await drain()
  assert.strictEqual(toasts[toasts.length - 1].title, '提交人数较多，请稍后再试')
  assert.strictEqual(toasts[toasts.length - 1].icon, 'none')
  assert.strictEqual(busy.data.networkError, false)
  assert.strictEqual(busy.data.submitting, false)
  assert.strictEqual(busy.data.phase, 'form')
  assert.strictEqual(busy.data.name, '测试片')
  assert.strictEqual(busy.data.platform, '测试平台')
  assert.strictEqual(busy.data.note, '保留备注')
  assert.strictEqual(busy.data.phone, '13912345678')
  answer(201, { id: 'id-again', no: 'QZ-AGAIN' })
  busy.submit()
  await drain()
  assert.strictEqual(busy.data.phase, 'submitted')
  assert.strictEqual(busy.data.no, 'QZ-AGAIN')
  assert.strictEqual(calls[calls.length - 1].method, 'POST')

  var network = form()
  network.onPhone({ detail: { value: '13912345678' } })
  network.onNote({ detail: { value: '保留备注' } })
  failNetwork()
  network.submit()
  await drain()
  assert.strictEqual(network.data.networkError, true)
  assert.strictEqual(network.data.phase, 'form')
  assert.strictEqual(network.data.name, '测试片')
  assert.strictEqual(network.data.platform, '测试平台')
  assert.strictEqual(network.data.note, '保留备注')
  assert.strictEqual(network.data.phone, '13912345678')
  assert.strictEqual(network.data.submitting, false)

  var doubled = form()
  doubled.onPhone({ detail: { value: '13912345678' } })
  var held = null
  var saved = wx.request
  wx.request = function (opt) {
    calls.push(opt)
    held = opt
  }
  var countBefore = calls.length
  doubled.submit()
  doubled.submit()
  doubled.submit()
  assert.strictEqual(calls.length, countBefore + 1, 'submit is single-flight')
  assert.strictEqual(doubled.data.submitting, true)
  assert.strictEqual(calls[calls.length - 1].url, '/test-api/api/v1/evidence-requests')
  assert.strictEqual(calls[calls.length - 1].method, 'POST')
  assert.strictEqual(calls[calls.length - 1].header.Authorization, 'Bearer evidence-token')
  assert.strictEqual(calls[calls.length - 1].data.phone, '13912345678')
  assert.strictEqual(calls[calls.length - 1].data.platform, '测试平台')
  held.success({ statusCode: 201, data: { id: 'id-1', no: 'QZ202611220099' } })
  wx.request = saved
  await drain()
  assert.strictEqual(doubled.data.submitting, false)
  assert.strictEqual(doubled.data.phase, 'submitted')
  assert.strictEqual(doubled.data.no, 'QZ202611220099')
  doubled.open()
  assert.strictEqual(navigated[navigated.length - 1], '/pages/evidence-detail/index?id=' + encodeURIComponent('id-1'))

  var dup = form()
  dup.onPhone({ detail: { value: '13912345678' } })
  answer(200, { code: 'duplicate', id: 'old-id', no: 'QZ-OLD', message: '已有申请' })
  dup.submit()
  await drain()
  assert.strictEqual(dup.data.showDuplicate, true)
  assert.strictEqual(dup.data.dupId, 'old-id')
  assert.strictEqual(dup.data.phase, 'form')
  assert.strictEqual(dup.data.name, '测试片')
  assert.strictEqual(dup.data.platform, '测试平台')
  assert.strictEqual(dup.data.phone, '13912345678')
  dup.closeDuplicate()
  assert.strictEqual(dup.data.showDuplicate, false)
  assert.strictEqual(dup.data.phase, 'form')
  assert.strictEqual(dup.data.name, '测试片')
  assert.strictEqual(dup.data.phone, '13912345678')
  dup.setData({ showDuplicate: true })
  dup.openExisting()
  assert.strictEqual(navigated[navigated.length - 1], '/pages/evidence-detail/index?id=' + encodeURIComponent('old-id'))

  var recent = open('pages/evidence/index', { kind: 'recent', name: encodeURIComponent('近期片') })
  recent.onPhone({ detail: { value: '13912345678' } })
  answer(201, { id: 'id-r', no: 'QZ-R' })
  recent.submit()
  await drain()
  assert.strictEqual(calls[calls.length - 1].data.kind, 'recent')
  assert.strictEqual(Object.prototype.hasOwnProperty.call(calls[calls.length - 1].data, 'platform'), false)
  assert.strictEqual(Object.prototype.hasOwnProperty.call(calls[calls.length - 1].data, 'phone'), true)

  var list = instantiate('pages/evidence-list/index')
  assert.strictEqual(list.data.loading, true)
  assert.strictEqual(list.data.rows.length, 0)
  var listed = null
  wx.request = function (opt) { listed = opt; calls.push(opt) }
  list.onShow()
  assert.strictEqual(list.data.loading, true)
  assert.strictEqual(list.data.rows.length, 0)
  assert.strictEqual(list.data.empty, false)
  listed.success({ statusCode: 200, data: [] })
  wx.request = saved
  await drain()
  assert.strictEqual(list.data.loading, false)
  assert.strictEqual(list.data.empty, true)
  assert.strictEqual(list.data.rows.length, 0)

  var rows = instantiate('pages/evidence-list/index')
  wx.request = function (opt) {
    calls.push(opt)
    opt.success({ statusCode: 200, data: [
      { id: 'a', no: 'QZ-A', name: '很长很长很长很长的节目名用来省略', platform: '测试平台', kind: 'normal', status: 'submitted', status_text: '已提交', created_at: '2026-11-22T09:00:00+08:00' },
      { id: 'b', no: 'QZ-B', name: '无平台', platform: null, kind: 'recent', status: 'done', status_text: '已完成', created_at: '2026-11-21T09:00:00+08:00' }
    ] })
  }
  rows.onShow()
  wx.request = saved
  await drain()
  assert.strictEqual(rows.data.rows[0].platform, '测试平台')
  assert.strictEqual(rows.data.rows[0].badge, '已提交')
  assert.strictEqual(rows.data.rows[1].platform, '')
  assert.strictEqual(rows.data.rows[1].badge, '已完成')
  assert.strictEqual(rows.data.empty, false)

  function openDetail(body) {
    answer(200, body)
    var page = open('pages/evidence-detail/index', { id: body.id })
    return drain().then(function () { return page })
  }
  var submitted = await openDetail(detailBody('submitted', { platform: '测试平台', phone: null, quote_amount: null }))
  assert.strictEqual(submitted.data.canWithdraw, true)
  assert.strictEqual(submitted.data.canConfirm, false)
  assert.strictEqual(submitted.data.phoneText, '—')
  assert.strictEqual(submitted.data.quoteText, '—')
  assert.strictEqual(submitted.data.badge, '已提交')
  var delivered = await openDetail(detailBody('delivered'))
  assert.strictEqual(delivered.data.canWithdraw, false)
  assert.strictEqual(delivered.data.canConfirm, true)
  var paid = await openDetail(detailBody('pay'))
  assert.strictEqual(paid.data.canWithdraw, false)
  assert.strictEqual(paid.data.canConfirm, false)
  assert.strictEqual(paid.data.badge, '待付款（线下）')

  var posts = 0
  wx.request = function (opt) {
    calls.push(opt)
    if (opt.method === 'POST') posts += 1
    opt.success({ statusCode: 200, data: detailBody('submitted') })
  }
  var modalCount = modals.length
  submitted.withdraw()
  assert.strictEqual(modals.length, modalCount + 1)
  var postBefore = posts
  modals[modals.length - 1].success({ confirm: false, cancel: true })
  assert.strictEqual(posts, postBefore, 'cancel withdraw sends nothing')
  modals[modals.length - 1].success({ confirm: true })
  assert.strictEqual(posts, postBefore + 1)
  await drain()
  wx.request = function (opt) {
    calls.push(opt)
    if (String(opt.url).indexOf('/withdraw') !== -1) {
      opt.success({ statusCode: 409, data: { code: 'invalid_state', message: '当前状态不能撤回' } })
      return
    }
    opt.success({ statusCode: 200, data: detailBody('accepted') })
  }
  var toastBefore = toasts.length
  submitted.withdraw()
  modals[modals.length - 1].success({ confirm: true })
  await drain()
  assert.strictEqual(toasts[toasts.length - 1].title, '状态已变化')
  assert.strictEqual(submitted.data.badge, '已受理')
  assert.strictEqual(submitted.data.canWithdraw, false)
  assert(toasts.length > toastBefore)

  wx.request = function (opt) {
    calls.push(opt)
    opt.success({ statusCode: 200, data: detailBody('done') })
  }
  var confirmModals = modals.length
  delivered.confirm()
  modals[modals.length - 1].success({ confirm: false })
  assert.strictEqual(modals.length, confirmModals + 1)
  assert.strictEqual(delivered.data.status ? delivered.data.badge : delivered.data.badge, '已交付')
  modals[modals.length - 1].success({ confirm: true })
  await drain()
  assert.strictEqual(delivered.data.badge, '已完成')
  assert.strictEqual(delivered.data.canConfirm, false)

  walk(root).forEach(function (file) {
    if (fs.readFileSync(file, 'utf8').indexOf('13800002610') !== -1) {
      throw new Error('banned phone in ' + path.relative(root, file))
    }
  })
  console.log('evidence api checks ok')
}

function walk(dir, out) {
  out = out || []
  fs.readdirSync(dir).forEach(function (name) {
    var full = path.join(dir, name)
    if (fs.statSync(full).isDirectory()) walk(full, out)
    else out.push(full)
  })
  return out
}

main().catch(function (error) {
  console.error(error && error.stack ? error.stack : error)
  process.exit(1)
})

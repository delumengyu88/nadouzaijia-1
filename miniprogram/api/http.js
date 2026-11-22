var config = require('../config')
var shape = require('./shape')
var state = require('./state')

function unavailable() {
  return Promise.reject(new Error('服务暂不可用'))
}

function request(path, data) {
  var base = String(config.apiBase || '').replace(/\/$/, '')
  if (!base) return Promise.reject(new Error('未配置服务地址'))
  return new Promise(function (resolve, reject) {
    wx.request({
      url: base + path,
      method: 'GET',
      timeout: 15000,
      data: data || {},
      header: { 'X-User-Id': state.userId() },
      success: function (res) {
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(res.data || {})
        else reject(new Error('服务暂不可用'))
      },
      fail: function () { reject(new Error('网络异常，请稍后再试')) }
    })
  })
}

function notReady() {
  return Promise.resolve({ ok: false, message: '服务暂不可用，请改用本地数据' })
}

module.exports = {
  bootstrap: function () { state.load() },
  reset: function () { state.reset() },
  splashSeen: function () { return true },
  markSplashSeen: function () {},
  markAnnounceSeen: function () {},
  homeState: function () {
    return {
      loggedIn: !!state.userId(),
      tier: 'free',
      banner: state.userId() ? 'free' : 'guest',
      bannerText: state.userId() ? '尚未开通会员' : '未登录',
      runningId: '',
      showAnnounce: false,
      showDone: false,
      doneText: '',
      doneQueryId: '',
      examples: [],
      platformTotal: 0,
      draft: ''
    }
  },
  login: function () {
    var session = state.load()
    if (session.current === 'guest') session.current = 'free'
    state.save()
    return { guest: false, tier: 'free', title: '免费用户 · 未开通会员', badge: '', lines: [] }
  },
  switchAccount: function () { return this.login() },
  accountKind: function () { return state.currentId() },
  profile: function () {
    if (!state.userId()) return { guest: true, tier: 'guest', title: '', badge: '', lines: [] }
    return { guest: false, tier: 'free', title: '免费用户 · 未开通会员', badge: '', lines: [] }
  },
  memberCenter: function () {
    return { guest: !state.userId(), tier: 'free', button: '去开通', intro: '开通结果以服务端订单为准。', showGuestTip: !state.userId() }
  },
  setDraft: function (value) { state.load().draft = String(value || ''); state.save() },
  getDraft: function () { return state.load().draft || '' },
  parseTitles: function (raw) { return require('../utils/text').parseTitles(raw) },
  parseFile: function () { return { ok: false, message: '请改为粘贴节目名' } },
  createQuery: function () { return notReady() },
  getQuery: function () { return null },
  advanceQuery: function () { return null },
  markQuerySeen: function () {},
  retryItem: function () { return null },
  retryAll: function () { return null },
  listRecords: function () { return [] },
  searchProgram: function (name) {
    return request('/api/v1/programs/search', { name: name }).then(function (body) {
      return shape.sanitizeSearch(body)
    })
  },
  exampleReport: function () { return null },
  programMeta: function (name) { return { name: name, type: '', year: '' } },
  tierOfAccount: function () { return state.userId() ? 'free' : 'guest' },
  checkout: function () { return notReady() },
  getOrder: function () { return null },
  listOrders: function () { return [] },
  startPay: function () { return notReady() },
  setOrderStatus: function () { return null },
  settle: function () { return notReady() },
  checkStatus: function () { return null },
  repay: function () { return notReady() },
  inviteState: function () { return { guest: !state.userId(), bound: false } },
  redeem: function () { return notReady() },
  listEvidence: function () { return [] },
  evidenceDetail: function () { return null },
  createEvidence: function () { return notReady() },
  withdrawEvidence: function () { return notReady() },
  confirmEvidence: function () { return notReady() },
  agreements: function () {
    return [
      { id: 'user', title: '用户协议' },
      { id: 'privacy', title: '隐私指引' },
      { id: 'member', title: '会员服务协议' }
    ]
  },
  agreement: function (id) {
    return {
      title: '协议',
      paragraphs: ['运营主体：那豆在架', '完整文本由服务端下发。']
    }
  },
  exportBook: function () { return { ok: false, message: '当前服务未提供导出' } },
  maskPhone: function (phone) { return phone }
}

void unavailable

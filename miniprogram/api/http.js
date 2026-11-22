var shape = require('./shape')
var state = require('./state')
var request = require('./request')
var evidence = require('./evidence')

function notReady() {
  return Promise.resolve({ ok: false, message: '服务暂不可用，请改用本地数据' })
}

module.exports = {
  bootstrap: function () { state.load() },
  reset: function () { state.reset() },
  splashSeen: function () { return require('./splash').seen() },
  markSplashSeen: function () { require('./splash').markSeen() },
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
      draft: state.load().draft || ''
    }
  },
  login: require('./auth').login,
  switchAccount: function () { return this.profile() },
  accountKind: function () { return state.currentId() },
  profile: function () { return require('./mock').profile() },
  memberCenter: function () { return require('./mock').memberCenter() },
  membershipPlans: function () { return require('./mock').membershipPlans() },
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
  listEvidence: function () {
    return request('/api/v1/evidence-requests', null, { method: 'GET', readBody: true }).then(function (body) {
      var rows = evidence.listRows(body)
      if (!rows) return Promise.reject(new Error('网络异常，请重试'))
      return rows.map(evidence.presentList)
    })
  },
  evidenceDetail: function (id) {
    return request('/api/v1/evidence-requests/' + encodeURIComponent(id || ''), null, { method: 'GET', readBody: true }).then(function (body) {
      if (!body || typeof body !== 'object' || Array.isArray(body) || body.code || !body.id) return null
      return evidence.presentDetail(body)
    }, function () { return null })
  },
  createEvidence: function (form) {
    return request('/api/v1/evidence-requests', evidence.createPayload(form || {}), { method: 'POST', readBody: true }).then(function (body) {
      return evidence.createOutcome(body)
    }, function () {
      return evidence.createOutcome(null)
    })
  },
  withdrawEvidence: function (id) { return evidence.postAction(request, id, 'withdraw') },
  confirmEvidence: function (id) { return evidence.postAction(request, id, 'confirm') },
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

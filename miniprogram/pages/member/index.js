var api = require('../../api/index')
var nav = require('../../utils/nav')
var member = require('../../utils/member')
var testOffer = require('../../api/live-test-offer')
var diamond = require('../../api/diamond')
var state = require('../../api/state')

function defaultPlanId(tier) {
  if (tier === 'regular') return 'membership_regular'
  if (tier === 'vip' || tier === 'free') return 'membership_vip'
  return ''
}

function presentCenter(info) {
  if (!info || info.guest || info.heading) return info
  var names = { vip: 'VIP', regular: '普通会员', free: '免费用户' }
  return Object.assign({}, info, {
    heading: names[info.tier] || '',
    detail: info.statusLine || '',
    extraLine: info.extraLine || ''
  })
}

function selectedPlan(plans, selected) {
  var found = null
  ;(plans || []).forEach(function (item) {
    if (item.id === selected) found = item
  })
  return found
}

Page(require('../../api/splash').page({
  data: {
    title: '会员中心',
    selected: '',
    showPay: false,
    payLabel: '',
    plans: [],
    statusLine: '',
    loginLoading: false,
    loginError: '',
    loading: require('../../config').mode !== 'mock',
    loadError: false
  },
  onIdentityReady: function () { this.onShow() },
  onShow: function () {
    require('../../utils/login').onShow(this)
    this.setData({ testBuild: testOffer.dedicated() })
    this.refreshSpecialOffer()
    var info = this.readCenter()
    var self = this
    this._seq = (this._seq || 0) + 1
    var seq = this._seq
    if (info && typeof info.then === 'function') {
      var waiting = nav.layout('member', this.query)
      waiting.title = '会员中心'
      waiting.loading = true
      waiting.loadError = false
      waiting.statusLine = ''
      waiting.plans = []
      waiting.showPay = false
      waiting.payLabel = ''
      waiting.guest = false
      waiting.extraLine = ''
      this.setData(waiting)
      nav.selectTab(this, 1)
      info.then(function (center) {
        if (seq !== self._seq) return
        self.paint(center)
      }, function () {
        if (seq !== self._seq) return
        self.paint({ guest: false, tier: '', statusLine: '', extraLine: '' })
        self.setData({ loading: false, loadError: true, guest: false })
      })
      return
    }
    this.paint(info)
    nav.selectTab(this, 1)
  },
  readCenter: function () {
    if (require('../../config').mode === 'mock') return api.memberCenter()
    var identity = require('../../api/http').memberCenter()
    if (testOffer.dedicated() || require('../../config').mode !== 'live' || !identity || typeof identity.then !== 'function') return identity
    return identity.then(function (info) {
      if (info.guest) return info
      var state = require('../../api/state')
      var token = state.token()
      return require('../../api/request')('/api/v1/pay/tiers').then(function (body) {
        if (token !== state.token()) throw new Error('登录状态已变化')
        if (!body || !Array.isArray(body.tiers)) throw new Error('会员档位暂不可用')
        return Object.assign({}, info, { pricedTiers: body.tiers })
      })
    })
  },
  paint: function (info) {
    info = presentCenter(info)
    var data = nav.layout('member', this.query)
    var catalog = testOffer.dedicated() ? [] : api.membershipPlans()
    if (require('../../config').mode === 'live') {
      var tiers = info.pricedTiers || []
      catalog = catalog.map(function (plan) {
        if (!plan.selectable) return plan
        var priced = member.pricedTier(tiers, plan.id)
        if (!priced) {
          return Object.assign({}, plan, { selectable: !!info.guest, price: null, priceText: '' })
        }
        return Object.assign({}, plan, priced, { priceText: member.priceText(priced) })
      })
    }
    var plans = member.decoratePlans(catalog, info.tier)
    var plan = selectedPlan(plans, this.data.selected)
    if (!plan || !plan.selectable) plan = selectedPlan(plans, defaultPlanId(info.tier))
    if (plan && !plan.selectable) plan = null
    data.title = '会员中心'
    data.plans = plans
    data.selected = plan ? plan.id : ''
    data.showPay = !!plan
    data.payLabel = plan ? member.confirmLabel(plan) : ''
    data.loading = false
    data.loadError = false
    Object.keys(info).forEach(function (key) { data[key] = info[key] })
    this.setData(data)
  },
  onHide: function () { this.clearTestOffer() },
  onUnload: function () { this.clearTestOffer() },
  clearTestOffer: function () {
    this._offerSeq = (this._offerSeq || 0) + 1
    this._offerSession = ''
    if (this.data.showTestOffer || this.data.showDiamondOffer) this.setData({ showTestOffer: false, showDiamondOffer: false })
  },
  refreshSpecialOffer: function () {
    this.clearTestOffer()
    var offer = diamond.enabled() ? diamond : testOffer
    if (!offer.enabled() || !state.token()) return
    var token = state.token()
    var seq = this._offerSeq
    var self = this
    offer.discover().then(function (productId) {
      if (!productId || seq !== self._offerSeq || state.token() !== token) return
      self._offerSession = token
      self.setData({ showTestOffer: offer === testOffer, showDiamondOffer: offer === diamond })
    })
  },
  openDiamond: function () {
    if (!this.data.showDiamondOffer || !diamond.enabled() || !this._offerSession || this._offerSession !== state.token()) {
      this.clearTestOffer()
      return
    }
    wx.navigateTo({ url: '/pages/pay/index?product=' + diamond.id })
  },
  openTestOffer: function () {
    if (!this.data.showTestOffer || !testOffer.enabled() || !this._offerSession || this._offerSession !== state.token()) {
      this.clearTestOffer()
      return
    }
    wx.navigateTo({ url: '/pages/pay/index?product=' + testOffer.id })
  },
  retryLoad: function () { this.onShow() },
  pick: function (e) {
    var plan = selectedPlan(this.data.plans, e.currentTarget.dataset.id)
    if (!plan || !plan.selectable) return
    this.setData({
      selected: plan.id,
      showPay: true,
      payLabel: member.confirmLabel(plan)
    })
  },
  go: function () {
    if (testOffer.dedicated() || !this.data.showPay || !this.data.selected) return
    if (this.data.guest) return require('../../utils/login')(this, this.onShow)
    if (require('../../utils/trial').blockPayment()) return
    wx.navigateTo({ url: '/pages/pay/index?product=' + this.data.selected })
  },
  openInvite: function () {
    if (this.data.guest) return require('../../utils/login')(this, this.onShow)
    wx.navigateTo({ url: '/pages/invite/index' })
  }
}))

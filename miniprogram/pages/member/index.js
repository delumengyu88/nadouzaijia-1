var api = require('../../api/index')
var nav = require('../../utils/nav')
var member = require('../../utils/member')

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

Page({
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
  onShow: function () {
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
    if (require('../../config').mode !== 'live' || !identity || typeof identity.then !== 'function') return identity
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
    var catalog = api.membershipPlans()
    if (require('../../config').mode === 'live') {
      var tiers = info.pricedTiers || []
      catalog = catalog.map(function (plan) {
        if (!plan.selectable) return plan
        var row = tiers.find(function (tier) { return tier.id === plan.id })
        if (!row || !Number.isSafeInteger(row.amount_fen) || row.amount_fen <= 0 || !Number.isSafeInteger(row.duration_days) || row.duration_days <= 0) {
          return Object.assign({}, plan, { selectable: !!info.guest, price: null, priceText: '' })
        }
        var price = row.amount_fen / 100
        return Object.assign({}, plan, { price: price, days: row.duration_days, priceText: '¥' + price + '/' + row.duration_days + '天' })
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
    data.payLabel = plan ? (info.guest && plan.price == null ? '登录后查看价格' : member.confirmLabel(plan.price)) : ''
    data.loading = false
    data.loadError = false
    Object.keys(info).forEach(function (key) { data[key] = info[key] })
    this.setData(data)
  },
  retryLoad: function () { this.onShow() },
  pick: function (e) {
    var plan = selectedPlan(this.data.plans, e.currentTarget.dataset.id)
    if (!plan || !plan.selectable) return
    this.setData({
      selected: plan.id,
      showPay: true,
      payLabel: this.data.guest && plan.price == null ? '登录后查看价格' : member.confirmLabel(plan.price)
    })
  },
  go: function () {
    if (!this.data.showPay || !this.data.selected) return
    if (this.data.guest) return require('../../utils/login')(this, this.onShow)
    if (require('../../utils/trial').blockPayment()) return
    wx.navigateTo({ url: '/pages/pay/index?product=' + this.data.selected })
  },
  openInvite: function () {
    if (this.data.guest) return require('../../utils/login')(this, this.onShow)
    wx.navigateTo({ url: '/pages/invite/index' })
  }
})

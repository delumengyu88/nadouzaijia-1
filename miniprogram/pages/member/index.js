var api = require('../../api/index')
var nav = require('../../utils/nav')
var member = require('../../utils/member')

function markPlans(plans, tier) {
  var currentId = tier === 'vip' ? 'membership_vip' : tier === 'regular' ? 'membership_regular' : tier === 'free' ? 'free' : ''
  return (plans || []).map(function (plan) {
    var next = Object.assign({}, plan, { current: !!currentId && plan.id === currentId })
    if (!plan.selectable) next.priceText = ''
    return next
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
    return require('../../api/http').memberCenter()
  },
  paint: function (info) {
    var data = nav.layout('member', this.query)
    var plans = markPlans(api.membershipPlans(), info.tier)
    var plan = selectedPlan(plans, this.data.selected)
    if (plan && !plan.selectable) plan = null
    data.title = '会员中心'
    data.plans = plans
    data.selected = plan ? plan.id : ''
    data.showPay = !!plan
    data.payLabel = plan ? member.confirmLabel(plan.price) : ''
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
      payLabel: member.confirmLabel(plan.price)
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

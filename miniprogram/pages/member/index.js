var api = require('../../api/index')
var nav = require('../../utils/nav')
var member = require('../../utils/member')

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
    loginError: ''
  },
  onShow: function () {
    var data = nav.layout('member', this.query)
    var info = api.memberCenter()
    var plans = api.membershipPlans()
    var plan = selectedPlan(plans, this.data.selected)
    data.title = '会员中心'
    data.plans = plans
    data.selected = plan ? plan.id : ''
    data.showPay = !!plan
    data.payLabel = plan ? member.confirmLabel(plan.price) : ''
    Object.keys(info).forEach(function (key) { data[key] = info[key] })
    this.setData(data)
    nav.selectTab(this, 1)
  },
  pick: function (e) {
    var plan = selectedPlan(this.data.plans, e.currentTarget.dataset.id)
    if (!plan) return
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

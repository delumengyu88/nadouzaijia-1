var api = require('../../api/index')
var nav = require('../../utils/nav')

function labelFor(tier, selected, guest) {
  if (guest) return '登录后开通'
  if (selected === 'membership_regular') return '开通普通会员 · ¥299'
  if (tier === 'vip') return '续费 VIP · ¥599'
  return '开通 VIP · ¥599'
}

Page({
  data: { title: '会员中心', selected: 'membership_vip' },
  onShow: function () {
    var data = nav.layout('member', this.query)
    var info = api.memberCenter()
    data.title = '会员中心'
    data.selected = this.data.selected || 'membership_vip'
    Object.keys(info).forEach(function (key) { data[key] = info[key] })
    data.payLabel = labelFor(data.tier, data.selected, data.guest)
    this.setData(data)
    nav.selectTab(this, 1)
  },
  pick: function (e) {
    var selected = e.currentTarget.dataset.id
    this.setData({
      selected: selected,
      payLabel: labelFor(this.data.tier, selected, this.data.guest)
    })
  },
  go: function () {
    if (require('../../utils/trial').blockPayment()) return
    if (this.data.guest) {
      api.login()
      this.onShow()
      wx.showToast({ title: '已登录', icon: 'none' })
      return
    }
    wx.navigateTo({ url: '/pages/pay/index?product=' + this.data.selected })
  }
})

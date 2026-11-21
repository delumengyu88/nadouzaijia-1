var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { title: '个人中心', showSheet: false },
  onShow: function () {
    var data = nav.layout()
    var info = api.profile()
    data.title = '个人中心'
    data.showSheet = false
    Object.keys(info).forEach(function (key) { data[key] = info[key] })
    this.setData(data)
    nav.selectTab(this, 2)
  },
  login: function () {
    api.login()
    this.onShow()
    wx.showToast({ title: '已登录', icon: 'none' })
  },
  go: function (e) {
    wx.navigateTo({ url: e.currentTarget.dataset.url })
  },
  openSheet: function () { this.setData({ showSheet: true }) },
  closeSheet: function () { this.setData({ showSheet: false }) },
  useAccount: function (e) {
    api.switchAccount(e.currentTarget.dataset.id)
    this.onShow()
  }
})

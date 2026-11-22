var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { useMock: require('../../config').mode !== 'live', title: '个人中心', showSheet: false },
  onShow: function () {
    var data = nav.layout('profile', this.query)
    var info = api.profile()
    data.title = '个人中心'
    data.showSheet = false
    data.useMock = require('../../config').mode === 'mock'
    Object.keys(info).forEach(function (key) { data[key] = info[key] })
    this.setData(data)
    nav.selectTab(this, 2)
  },
  login: function () {
    return require('../../utils/login')(this, this.onShow)
  },
  go: function (e) {
    wx.navigateTo({ url: e.currentTarget.dataset.url })
  },
  noop: function () {},
  openSheet: function () { this.setData({ showSheet: true }) },
  closeSheet: function () { this.setData({ showSheet: false }) },
  useAccount: function (e) {
    api.switchAccount(e.currentTarget.dataset.id)
    this.onShow()
  }
})

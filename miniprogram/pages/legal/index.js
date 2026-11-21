var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { title: '协议与隐私', showBack: true, rows: [] },
  onLoad: function () {
    var data = nav.layout()
    data.title = '协议与隐私'
    data.showBack = true
    data.rows = api.agreements()
    this.setData(data)
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  open: function (e) {
    wx.navigateTo({ url: '/pages/legal-doc/index?id=' + e.currentTarget.dataset.id })
  }
})

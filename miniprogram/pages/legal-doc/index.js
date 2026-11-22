var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { title: '协议', showBack: true, paragraphs: [] },
  onLoad: function (query) {
    var doc = api.agreement(query.id)
    var layout = nav.layout('legal-doc', this.query)
    this.setData({
      statusBarHeight: layout.statusBarHeight,
      navHeight: layout.navHeight, trialData: layout.trialData,
      showBack: true,
      title: doc ? doc.title : '协议',
      paragraphs: doc ? doc.paragraphs : []
    })
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  }
})

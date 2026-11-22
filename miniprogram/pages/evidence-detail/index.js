var api = require('../../api/index')
var nav = require('../../utils/nav')

Page({
  data: { title: '取证进度', showBack: true },
  onLoad: function (query) {
    this.id = query.id || ''
    var layout = nav.layout('evidence-detail', this.query)
    this.setData({ statusBarHeight: layout.statusBarHeight, navHeight: layout.navHeight, trialData: layout.trialData, showBack: true, title: '取证进度' })
    this.load()
  },
  load: function () {
    var detail = api.evidenceDetail(this.id)
    if (!detail) return
    var layout = nav.layout('evidence-detail', this.query)
    detail.statusBarHeight = layout.statusBarHeight
    detail.navHeight = layout.navHeight
    detail.showBack = true
    detail.title = '取证进度'
    this.setData(detail)
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  withdraw: function () {
    var result = api.withdrawEvidence(this.id)
    if (result.detail) this.setData(result.detail)
  },
  confirm: function () {
    var result = api.confirmEvidence(this.id)
    if (result.detail) this.setData(result.detail)
  }
})

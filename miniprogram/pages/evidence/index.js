var api = require('../../api/index')
var nav = require('../../utils/nav')

function decode(value) {
  if (!value) return ''
  try { return decodeURIComponent(value) } catch (err) { return value }
}

Page({
  data: { title: '委托取证', showBack: true, phase: 'form', phone: '13800002610', phoneShown: '138****2610', note: '' },
  onLoad: function (query) {
    var layout = nav.layout('evidence', this.query)
    var kind = query.kind === 'recent' ? 'recent' : 'normal'
    this.setData({
      statusBarHeight: layout.statusBarHeight,
      navHeight: layout.navHeight,
      showBack: true,
      title: '委托取证',
      phase: query.view === 'duplicate' ? 'duplicate' : 'form',
      kind: kind,
      kindText: kind === 'recent' ? '近期取证' : '常规取证',
      name: decode(query.name) || '雾港来信',
      platform: kind === 'recent' ? '' : decode(query.platform),
      note: kind === 'recent' ? '请尽量取近期播出记录。' : '',
      dupId: ''
    })
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  onName: function (e) { this.setData({ name: e.detail.value }) },
  onPlatform: function (e) { this.setData({ platform: e.detail.value }) },
  onNote: function (e) { this.setData({ note: e.detail.value }) },
  submit: function () {
    var result = api.createEvidence({
      name: this.data.name,
      note: this.data.note,
      phone: this.data.phone,
      platform: this.data.platform,
      kind: this.data.kind
    })
    if (!result.ok && result.code === 'duplicate') {
      this.setData({ phase: 'duplicate', dupId: result.id })
      return
    }
    if (!result.ok) {
      wx.showToast({ title: result.message, icon: 'none' })
      return
    }
    this.setData({ phase: 'submitted', no: result.no, newId: result.id })
  },
  open: function () {
    var id = this.data.dupId || this.data.newId
    wx.navigateTo({ url: '/pages/evidence-detail/index?id=' + id })
  }
})

var api = require('../../api/index')
var nav = require('../../utils/nav')

function decode(value) {
  if (!value) return ''
  try { return decodeURIComponent(value) } catch (err) { return value }
}

function compact(value) {
  return String(value || '').replace(/[\s\u3000]/g, '')
}

function trimEnds(value) {
  return String(value || '').replace(/^[\s\u3000]+|[\s\u3000]+$/g, '')
}

Page({
  data: {
    title: '委托取证',
    showBack: true,
    phase: 'form',
    phone: '',
    note: '',
    submitting: false,
    phoneError: '',
    phoneFocus: false,
    networkError: false,
    showDuplicate: false
  },
  onLoad: function (query) {
    var layout = nav.layout('evidence', query)
    var kind = query.kind === 'recent' ? 'recent' : 'normal'
    var sample = ''
    if (!api.isLive && typeof api.evidenceSampleName === 'function') sample = api.evidenceSampleName()
    this.setData({
      statusBarHeight: layout.statusBarHeight,
      navHeight: layout.navHeight,
      showBack: true,
      title: '委托取证',
      phase: 'form',
      kind: kind,
      kindText: kind === 'recent' ? '近期取证' : '常规取证',
      name: decode(query.name) || sample,
      platform: kind === 'recent' ? '' : decode(query.platform),
      note: kind === 'recent' && !api.isLive ? '请尽量取近期播出记录。' : '',
      phone: '',
      phoneError: '',
      phoneFocus: false,
      submitting: false,
      networkError: false,
      showDuplicate: false,
      dupId: '',
      nameError: ''
    })
  },
  back: function () {
    if (getCurrentPages().length > 1) wx.navigateBack()
    else wx.switchTab({ url: '/pages/profile/index' })
  },
  onName: function (e) { this.setData({ name: e.detail.value, nameError: '' }) },
  onPlatform: function (e) { this.setData({ platform: e.detail.value }) },
  onNote: function (e) { this.setData({ note: e.detail.value }) },
  onPhone: function (e) { this.setData({ phone: e.detail.value, phoneError: '' }) },
  markPhone: function (message) {
    this.setData({ phoneError: message, phoneFocus: false, submitting: false })
    this.setData({ phoneFocus: true })
  },
  submit: function () {
    if (this.data.submitting) return
    var phone = compact(this.data.phone)
    if (!phone) {
      this.markPhone('请填写手机号')
      return
    }
    if (!/^1\d{10}$/.test(phone)) {
      this.markPhone('请输入 11 位手机号')
      return
    }
    var name = trimEnds(this.data.name)
    if (!name) {
      this.setData({ nameError: '请填写节目名', submitting: false })
      return
    }
    this.setData({ submitting: true, phoneError: '', networkError: false, nameError: '' })
    var result = api.createEvidence({
      name: name,
      note: this.data.note,
      phone: phone,
      platform: this.data.kind === 'recent' ? '' : this.data.platform,
      kind: this.data.kind
    })
    if (result && typeof result.then === 'function') {
      var self = this
      return result.then(function (res) { self.finishSubmit(res) }, function () {
        self.finishSubmit({ ok: false, code: 'network' })
      })
    }
    this.finishSubmit(result)
  },
  finishSubmit: function (result) {
    result = result || {}
    if (result.ok) {
      this.setData({
        submitting: false,
        phase: 'submitted',
        no: result.no,
        newId: result.id,
        showDuplicate: false,
        networkError: false,
        phoneError: ''
      })
      return
    }
    if (result.code === 'duplicate') {
      this.setData({ submitting: false, showDuplicate: true, dupId: result.id || '', networkError: false })
      return
    }
    if (result.code === 'phone_required') {
      this.markPhone('请填写手机号')
      return
    }
    if (result.code === 'invalid_phone') {
      this.markPhone('请输入 11 位手机号')
      return
    }
    if (result.code === 'no_unavailable') {
      this.setData({ submitting: false, networkError: false })
      wx.showToast({ title: '提交人数较多，请稍后再试', icon: 'none' })
      return
    }
    this.setData({ submitting: false, networkError: true, showDuplicate: false })
  },
  open: function () {
    wx.navigateTo({ url: '/pages/evidence-detail/index?id=' + encodeURIComponent(this.data.newId || '') })
  },
  openExisting: function () {
    wx.navigateTo({ url: '/pages/evidence-detail/index?id=' + encodeURIComponent(this.data.dupId || '') })
  }
})

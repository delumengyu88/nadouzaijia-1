const url = require('../../endpoint')
Page({
  data: {busy: false, result: '', canCopy: false},
  probe() {
    if (this.data.busy) return
    this.raw = null
    this.setData({busy: true, result: '检查中', canCopy: false})
    const fail = (err) => {
      this.raw = err && typeof err.errMsg === 'string' ? err.errMsg : null
      const errno = err && err.errno !== undefined ? String(err.errno) : '未提供'
      this.setData({busy: false, result: '没有收到 HTTP 响应，errno=' + errno + '\n' +
        (this.raw || '微信未提供 errMsg'), canCopy: !!this.raw})
    }
    try {
      wx.request({url, method: 'GET', timeout: 15000,
        success: (res) => this.setData({busy: false, result: 'HTTP ' + res.statusCode +
          (res.statusCode === 200 && res.data && res.data.status === 'ok' ? '，健康正常' : '，健康响应不符')}),
        fail})
    } catch (err) { fail(err) }
  },
  copy() { if (this.raw) wx.setClipboardData({data: this.raw}) }
})

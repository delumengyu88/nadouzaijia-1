var privacy = require('../../utils/privacy')

Component({
  data: { show: false },
  lifetimes: {
    attached: function () {
      var self = this
      this._unsubscribe = privacy.subscribe(function () {
        self.setData({ show: true })
      })
    },
    detached: function () {
      if (this._unsubscribe) this._unsubscribe()
    }
  },
  methods: {
    noop: function () {},
    contractFailed: function () {
      wx.showToast({ title: '暂时无法打开隐私指引，请稍后再试', icon: 'none' })
    },
    openContract: function () {
      if (typeof wx.openPrivacyContract !== 'function') {
        this.contractFailed()
        return
      }
      var self = this
      try {
        wx.openPrivacyContract({
          fail: function () { self.contractFailed() }
        })
      } catch (err) {
        this.contractFailed()
      }
    },
    onAgree: function (e) {
      var buttonId = 'agree-btn'
      if (e && e.currentTarget && e.currentTarget.id) buttonId = e.currentTarget.id
      else if (e && e.target && e.target.id) buttonId = e.target.id
      else if (e && e.detail && e.detail.buttonId) buttonId = e.detail.buttonId
      privacy.consume().forEach(function (resolve) {
        resolve({ event: 'agree', buttonId: buttonId })
      })
      this.setData({ show: false })
    },
    onDisagree: function () {
      privacy.consume().forEach(function (resolve) {
        resolve({ event: 'disagree' })
      })
      this.setData({ show: false })
      wx.showToast({ title: '需要同意隐私指引才能选择文件', icon: 'none' })
    }
  }
})

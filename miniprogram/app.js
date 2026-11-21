var api = require('./api/index')

App({
  onLaunch: function () {
    api.bootstrap()
  }
})

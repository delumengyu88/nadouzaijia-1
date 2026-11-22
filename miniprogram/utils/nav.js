function layout(page, query) {
  var status = 20
  var nav = 44
  try {
    var sys = wx.getSystemInfoSync()
    status = sys.statusBarHeight || 20
    if (wx.getMenuButtonBoundingClientRect) {
      var menu = wx.getMenuButtonBoundingClientRect()
      if (menu && menu.top && menu.height) {
        nav = Math.max(44, (menu.top - status) * 2 + menu.height)
      }
    }
  } catch (err) {}
  return { statusBarHeight: status, navHeight: status + nav }
}

function selectTab(page, index) {
  if (typeof page.getTabBar === 'function' && page.getTabBar()) {
    page.getTabBar().setData({ selected: index })
  }
}

function later(ms, fn) {
  if (typeof wx !== 'undefined' && wx.__instant) {
    fn()
    return
  }
  setTimeout(fn, ms)
}

module.exports = { layout: layout, later: later, selectTab: selectTab }

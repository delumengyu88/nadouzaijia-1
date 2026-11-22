function layout(page, query) {
  var status = 20
  var bar = 44
  var capsule = false
  try {
    var sys = wx.getSystemInfoSync() || {}
    if (typeof sys.statusBarHeight === 'number') status = sys.statusBarHeight
    var menu = wx.getMenuButtonBoundingClientRect && wx.getMenuButtonBoundingClientRect()
    if (menu && menu.top > 0 && menu.height > 0) {
      var gap = menu.top - status
      if (gap < 0) gap = 0
      bar = gap * 2 + menu.height
      capsule = true
    }
  } catch (err) {}
  return {
    statusBarHeight: status,
    navBarHeight: bar,
    // 其他页面仍用不少于 44px 的导航条占位。首页会改成 statusBarHeight + navBarHeight，
    // 与导航条实际高度对齐，避免 88rpx 和 44px 下限把内容顶进导航条。
    navHeight: status + (capsule ? Math.max(44, bar) : bar)
  }
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

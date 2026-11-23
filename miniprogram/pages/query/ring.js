// 圆环比例只来自已经返回的条数。等待文案只跟当前这一次请求已过的时间比较，不显示剩余秒数。
var USUAL_WAIT = '通常 10–30 秒'
var SLOW_WAIT = '比平时慢一些，请稍候'

function deg(value) {
  return Math.round(value * 1000) / 1000
}

function counts(view) {
  var matched = /^已完成 (\d+) \/ (\d+)$/.exec(view.head || '')
  if (matched) return { done: Number(matched[1]), total: Number(matched[2]) }
  var items = Array.isArray(view.items) ? view.items : []
  var done = 0
  items.forEach(function (item) {
    if (item && (item.hit || item.missed)) done += 1
  })
  return { done: done, total: items.length }
}

function ringState(view) {
  view = view || {}
  var count = counts(view)
  var done = count.done
  var total = count.total
  if (done < 0) done = 0
  if (total < 0) total = 0
  if (total && done > total) done = total
  var ratio = total ? done / total : 0
  var right = ratio <= 0 ? -180 : (ratio >= 0.5 ? 0 : -180 + ratio * 360)
  var left = ratio <= 0.5 ? 180 : (ratio >= 1 ? 0 : 180 - (ratio - 0.5) * 360)
  var spinning = !!(view.running && ratio < 1)
  var mode = 'progress'
  if (total > 0 && ratio >= 1) mode = 'full'
  else if (spinning && ratio === 0) mode = 'spin'
  return {
    ringMode: mode,
    ringSpin: spinning,
    ringFull: mode === 'full',
    ringRatio: ratio,
    ringDone: done,
    ringTotal: total,
    ringText: done + '/' + total,
    ringRight: deg(right),
    ringLeft: deg(left)
  }
}

function waitCopy(running, startedAt, now, slowMs) {
  if (!running || !startedAt || !(slowMs > 0) || !(now >= startedAt)) return USUAL_WAIT
  if (now - startedAt >= slowMs) return SLOW_WAIT
  return USUAL_WAIT
}

module.exports = {
  ringState: ringState,
  waitCopy: waitCopy,
  usualWait: USUAL_WAIT,
  slowWait: SLOW_WAIT
}

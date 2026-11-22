// 圆环比例只来自已经返回的条数（已完成/总数）。
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
  var mode = 'progress'
  if (total > 0 && ratio >= 1) mode = 'full'
  else if (view.running && ratio === 0) mode = 'spin'
  return {
    ringMode: mode,
    ringSpin: mode === 'spin',
    ringFull: mode === 'full',
    ringRatio: ratio,
    ringText: done + '/' + total,
    ringRight: deg(right),
    ringLeft: deg(left)
  }
}

module.exports = { ringState: ringState }

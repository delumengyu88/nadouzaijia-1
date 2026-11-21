function pad2(n) {
  n = String(n)
  return n.length < 2 ? '0' + n : n
}

function today() {
  var shifted = new Date(Date.now() + 8 * 3600 * 1000)
  return shifted.getUTCFullYear() + '-' + pad2(shifted.getUTCMonth() + 1) + '-' + pad2(shifted.getUTCDate())
}

function addDays(ymd, days) {
  var parts = String(ymd).split('-')
  var dt = new Date(Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]) + days))
  return dt.getUTCFullYear() + '-' + pad2(dt.getUTCMonth() + 1) + '-' + pad2(dt.getUTCDate())
}

function daysAgo(n) {
  return addDays(today(), -n)
}

function active(ymd) {
  return !!(ymd && ymd >= today())
}

function daysBetween(fromYmd, toYmd) {
  var a = fromYmd.split('-')
  var b = toYmd.split('-')
  var da = Date.UTC(Number(a[0]), Number(a[1]) - 1, Number(a[2]))
  var db = Date.UTC(Number(b[0]), Number(b[1]) - 1, Number(b[2]))
  return Math.round((db - da) / 86400000)
}

module.exports = {
  pad2: pad2,
  today: today,
  addDays: addDays,
  daysAgo: daysAgo,
  active: active,
  daysBetween: daysBetween
}

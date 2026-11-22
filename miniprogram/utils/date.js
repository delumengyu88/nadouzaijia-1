function pad2(n) {
  n = String(n)
  return n.length < 2 ? '0' + n : n
}

function today() {
  var shifted = new Date(Date.now() + 8 * 3600 * 1000)
  return shifted.getUTCFullYear() + '-' + pad2(shifted.getUTCMonth() + 1) + '-' + pad2(shifted.getUTCDate())
}

// API timestamps include an offset. A date-only value has no time to convert.
function beijingTime(value) {
  if (typeof value !== 'string') return ''
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/i.test(value)) return ''
  var time = Date.parse(value)
  if (!isFinite(time)) return ''
  return new Date(time + 8 * 3600 * 1000).toISOString().slice(0, 16).replace('T', ' ')
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
  beijingTime: beijingTime,
  addDays: addDays,
  daysAgo: daysAgo,
  active: active,
  daysBetween: daysBetween
}

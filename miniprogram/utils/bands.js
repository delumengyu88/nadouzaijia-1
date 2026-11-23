// 结果页时间轴（方案 B）：按记录距今时长分四档。档位、条数、解锁价和图片清晰度都来自服务端；
// 旧服务端没有 bands 字段时返回 null，页面沿用原来的平铺列表。
var beijingTime = require('./date').beijingTime
var OTHER = '其他平台'
var ORDER = ['ge_180', 'd90_179', 'd30_89', 'lt_30']
var LABELS = { ge_180: '180天以上', d90_179: '90–180天', d30_89: '30–90天', lt_30: '30天内' }

function rowOf(rec, index) {
  var platform = rec.platform
  var unknown = !platform || platform === OTHER || platform === '平台未识别'
  var checked = beijingTime(rec.checked_at)
  var view = rec.frame_view === 'clear' || rec.frame_view === 'blur' ? rec.frame_view : ''
  return {
    id: rec.record_id,
    platform: unknown ? OTHER : platform,
    unknown: unknown,
    sub: unknown ? '' : (rec.platform_subtype || ''),
    network: rec.network_type || '',
    frames: typeof rec.frame_count === 'number' ? '命中 ' + rec.frame_count + ' 帧' : '',
    date: checked ? checked.slice(0, 10) : '',
    times: (rec.timestamps || []).slice(0, 3),
    view: view,
    blur: view === 'blur',
    clear: view === 'clear',
    src: '',
    expanded: index === 0
  }
}

function distinctPlatforms(records) {
  var names = []
  records.forEach(function (rec) {
    var name = rec.platform
    if (name && name !== OTHER && name !== '平台未识别' && names.indexOf(name) === -1) names.push(name)
  })
  return names.length
}

function timeline(search) {
  if (!search || !Array.isArray(search.bands) || !search.bands.length || search.example || search.publicExample) return null
  var records = (search.records || []).filter(function (rec) { return rec && !rec.locked && !rec.summary_only })
  var tier = search.viewer_tier || 'free'
  var total = 0
  var seen = 0
  var anyLocked = false
  var bands = ORDER.map(function (key) {
    var src = search.bands.filter(function (band) { return band.band === key })[0]
    if (!src) return null
    var rows = src.state === 'open'
      ? records.filter(function (rec) { return rec.age_bucket === key }).map(rowOf)
      : []
    var count = src.count
    total += count
    if (src.state === 'open') seen += rows.length
    var offer = src.unlock
    var locked = src.state === 'locked'
    if (locked && count > 0) anyLocked = true
    var blur = src.frame_view === 'blur'
    return {
      key: key,
      label: src.label || LABELS[key],
      count: count,
      title: (src.label || LABELS[key]) + ' · ' + count + ' 条',
      open: src.state === 'open',
      locked: locked,
      evidence: src.state === 'evidence',
      empty: count === 0,
      status: src.state === 'open' ? (src.unlocked_by === 'unlock' ? '已解锁' : '已可看') : '',
      rows: rows,
      blur: blur,
      clear: src.frame_view === 'clear',
      viewNote: blur ? '模糊预览' : (src.frame_view === 'clear' ? '清晰截图 · 点击放大' : ''),
      product: locked && offer ? offer.product : '',
      buyText: locked && offer && offer.price_text ? offer.price_text + ' 解锁' : '',
      purchasable: !!(locked && offer && offer.purchasable),
      hint: locked ? (src.upgrade_hint || '') : ''
    }
  }).filter(Boolean)
  var platforms = search.platform_count > 0 ? search.platform_count : distinctPlatforms(records)
  var memberCta = ''
  if (anyLocked && tier === 'free') memberCta = '开通会员'
  else if (anyLocked && tier === 'regular') memberCta = '升级 VIP'
  return {
    bands: bands,
    tier: tier,
    line: '共 ' + total + ' 条 · ' + platforms + ' 个平台 · 你已看到 ' + seen + ' 条',
    total: total,
    seen: seen,
    memberCta: memberCta,
    memberProduct: tier === 'regular' ? 'membership_vip' : 'membership_regular',
    framesReady: search.frames_ready === true,
    reportReady: search.report_ready === true
  }
}

function findRow(view, id) {
  var found = null
  ;((view && view.bands) || []).forEach(function (band, b) {
    band.rows.forEach(function (row, r) {
      if (row.id === id) found = { band: band, row: row, b: b, r: r }
    })
  })
  return found
}

function pickRecentHit(rows) {
  if (!rows || !rows.length || !rows[0] || !rows[0].id) return null
  var queryId = rows[0].id
  for (var i = 0; i < rows.length; i += 1) {
    var row = rows[i]
    if (!row || row.id !== queryId) break
    if (row.badgeCls === 'ok' && row.name) return { id: row.id, name: row.name }
  }
  return null
}

// 首页「最近查询」：只展示最近一次查询里当前用户已经能看的记录，最多 3 条。
// 锁定档和 30 天内一律不进这一块。有分档数据时走时间轴的 open 档；旧服务端没有
// bands 时，只保留带明确档位、且不是 lt_30 的未锁定记录。没有可展示的记录则返回 null。
function homePreview(search, limit) {
  var max = typeof limit === 'number' && limit > 0 ? limit : 3
  if (!search || search.example || search.publicExample) return null
  if (search.outcome !== 'hit' && search.outcome !== 'recent_only') return null
  var rows = []
  var total = 0
  var view = timeline(search)
  if (view) {
    view.bands.forEach(function (band) {
      if (!band.open || band.key === 'lt_30') return
      total += band.count
      band.rows.forEach(function (row) { rows.push(row) })
    })
  } else {
    (search.records || []).forEach(function (rec) {
      if (!rec || rec.locked || rec.summary_only) return
      if (!rec.age_bucket || rec.age_bucket === 'lt_30' || rec.age_bucket === 'unknown') return
      rows.push(rowOf(rec, rows.length))
    })
    total = rows.length
  }
  if (!total || !rows.length) return null
  return {
    name: search.program_name || '',
    count: total,
    rows: rows.slice(0, max).map(function (row) {
      return Object.assign({}, row, { expanded: true })
    }),
    framesReady: !!(view && view.framesReady),
    blur: rows.slice(0, max).some(function (row) { return row.blur })
  }
}

module.exports = {
  timeline: timeline,
  findRow: findRow,
  pickRecentHit: pickRecentHit,
  homePreview: homePreview,
  LABELS: LABELS,
  ORDER: ORDER
}

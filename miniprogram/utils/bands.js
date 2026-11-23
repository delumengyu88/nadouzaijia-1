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

module.exports = { timeline: timeline, findRow: findRow, LABELS: LABELS, ORDER: ORDER }

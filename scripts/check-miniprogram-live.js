var assert = require('assert')
var config = require('../miniprogram/config')
config.calibrating = false
assert.strictEqual(config.mode, 'live')
// Exercise the explicitly selected development hybrid configuration.
config.mode = 'hybrid'
assert.strictEqual(config.apiBase, 'https://nadou.youroem.cn')
config.apiBase = '/test-api'
var calls = []
var nextError = false
var querySeq = 0
var withGenerated = false
var pendingVerification = false
var summaryReply = null
var oldBackend = false
var saved = []
var store = {}
global.wx = {
  getStorageSync: function (k) { return store[k] }, setStorageSync: function (k, v) { store[k] = v },
  getSystemInfoSync: function () { return { statusBarHeight: 20 } },
  request: function (opt) {
    var url = opt.url || ''
    if (url.indexOf('/api/v1/queries') !== -1 && /\/results(\/failed)?$/.test(url)) saved.push(opt)
    if (oldBackend && /\/programs\/search\/batch$/.test(url)) {
      calls.push(opt)
      opt.success({ statusCode: 404, data: { detail: 'Not Found' } })
      return
    }
    if (url.indexOf('/api/v1/queries') !== -1) {
      var creating = (opt.method || 'GET') === 'POST' && /\/api\/v1\/queries$/.test(url)
      querySeq += creating ? 1 : 0
      var id = creating ? 'live-query-' + querySeq : 'live-query'
      var found = url.match(/\/api\/v1\/queries\/([^/?]+)/)
      if (found) id = decodeURIComponent(found[1])
      opt.success({ statusCode: creating ? 201 : 200, data: {
        id: id, queued: true, running: false, done: false, head: '', percent: 0, items: [],
        networkError: null, showNetworkRetry: null, failBanner: null, seen_at: null
      } })
      return
    }
    calls.push(opt)
    if (nextError) { nextError = false; opt.fail(); return }
    opt.success({ statusCode: 200, data: opt.data.names ? opt.data.names.map(searchResult) : searchResult(opt.data.name) })
  }
}
function searchResult(name) {
  if (summaryReply) return summaryReply
  if (pendingVerification) {
    return { program_name: name, outcome: 'unverified',
      platform_groups: [], locked_records: [], unverified_source_count: 1 }
  }
  if (withGenerated) {
    // Server shape after generated dates: 其他平台 group arrives first here on purpose,
    // the generated-date row cannot be exported, and evidence_platforms omits it.
    return { program_name: name, outcome: 'hit', platform_count: 1,
      evidence_platforms: ['真实测试平台'], evidence_program_name: name,
      platform_groups: [
        { platform: '其他平台', records: [{ record_id: 'gen1', locked: false, platform: '其他平台', access: 'full',
          checked_at: '2026-07-01T12:00:00+08:00', can_download_report: false }] },
        { platform: '真实测试平台', records: [{ record_id: 'xh1', locked: false, platform: '真实测试平台', access: 'full',
          checked_at: '2026-01-02T03:04:05+08:00', can_download_report: true },
        { record_id: 'xh2', locked: false, platform: '真实测试平台', access: 'full',
          checked_at: '2026-01-03T03:04:05+08:00', can_download_report: true }] }],
      locked_records: [{ record_id: 'locked', locked: true, age_bucket: 'd30_89' }],
      locked_summary: { locked_total: 1, unlockable_total: 1, by_required_tier: { vip: 1 }, cheapest_full_unlock: 'single_program_30d' }
    }
  }
  return { program_name: name, outcome: 'hit', platform_count: 1, evidence_platforms: ['真实测试平台'], evidence_program_name: name,
    platform_groups: [{ records: [{ record_id: 'xh1', locked: false, platform: '真实测试平台', category: '测试类别',
      access: 'full', checked_at: '2026-01-02T03:04:05+08:00', can_download_report: true }] }],
    locked_records: [{ record_id: 'locked', locked: true, age_bucket: 'd30_89', platform: '禁止泄露' }],
    locked_summary: { locked_total: 1, unlockable_total: 1, by_required_tier: { vip: 1 }, cheapest_full_unlock: 'single_program_30d' }
  }
}
var api = require('../miniprogram/api')
var state = require('../miniprogram/api/state')
function drain() { return new Promise(function (resolve) { setImmediate(resolve) }) }
async function main() {
  api.reset(); state.setSession({ token: 'test-free-token', expires_at: new Date(Date.now() + 86400000).toISOString(), user: { id: 'free', tier: 'free' } })
  assert.strictEqual(api.isLive, true)
  assert.strictEqual(api.listEvidence().length, 0)
  var query = await api.createQuery(['真实测试片', '失败测试片'])
  assert(query.ok)
  api.advanceQuery(query.id); await drain()
  assert.strictEqual(calls[0].url, '/test-api/api/v1/programs/search/batch')
  assert.strictEqual(calls[0].header.Authorization, 'Bearer test-free-token')
  assert.deepStrictEqual(calls[0].data.names, ['真实测试片', '失败测试片'])
  var results = api.getQuery(query.id)
  assert(results.done && results.items.every(function (row) { return row.hit }))
  assert.strictEqual(results.items[0].evidencePlatform, '真实测试平台')
  // 旧后端没有批量接口（部署前或回滚后）：改回逐个检索并逐条保存结果，不能整批失败。
  oldBackend = true
  var before = calls.length
  var oldQuery = await api.createQuery(['旧接口甲', '旧接口乙'])
  api.advanceQuery(oldQuery.id)
  for (var spin = 0; spin < 10; spin++) await drain()
  var oldCalls = calls.slice(before).map(function (c) { return c.url })
  assert.deepStrictEqual(oldCalls, ['/test-api/api/v1/programs/search/batch',
    '/test-api/api/v1/programs/search', '/test-api/api/v1/programs/search'])
  var oldResults = api.getQuery(oldQuery.id)
  assert(oldResults.done && oldResults.items.every(function (row) { return row.hit }))
  assert.deepStrictEqual(saved.filter(function (c) { return c.url.indexOf(oldQuery.id) >= 0 })
    .map(function (c) { return [c.url.slice(-8), c.data.name, !!c.data.failed] }),
    [['/results', '旧接口甲', false], ['/results', '旧接口乙', false]])
  oldBackend = false
  state.setSession({ token: 'test-member-token', expires_at: new Date(Date.now() + 86400000).toISOString(), user: { id: 'member', tier: 'vip' } })
  var crossed = api.getQuery(query.id)
  assert(crossed && typeof crossed.then === 'function', 'another account must not read the in-memory job')
  var shape = require('../miniprogram/api/shape')
  var beijingTime = require('../miniprogram/utils/date').beijingTime
  assert.strictEqual(beijingTime('2026-11-20T17:15:10Z'), '2026-11-21 01:15')
  assert.strictEqual(beijingTime('2026-11-21T01:15:10+08:00'), '2026-11-21 01:15')
  assert.strictEqual(beijingTime(null), '')
  assert.strictEqual(beijingTime('2026-11-21'), '2026-11-21')
  var source = {
    outcome: 'hit', program_name: '真实测试片', records: [
      { record_id: 'xh1', platform: '实际平台', access: 'lowres', hd_url: 'https://img.invalid/full', thumbnail_url: 'https://img.invalid/low' },
      { record_id: 'xh2', access: 'full', thumbnail_url: 'signed://derived/unusable', hd_url: 'https://img.invalid/HD' },
      { record_id: 'xh3', access: 'full', original_url: 'https://img.invalid/original', thumbnail_url: 'http://img.invalid/unsafe', can_download_report: 'false', can_download_screenshot: 'false' },
      { record_id: 'closed', locked: true, hd_url: 'https://img.invalid/private' }
    ]
  }
  var clean = shape.sanitizeSearch(source)
  assert.strictEqual(clean.records[0].thumbnail_url, source.records[0].thumbnail_url)
  assert.strictEqual(clean.records[0].hd_url, undefined)
  assert.strictEqual(clean.records[1].hd_url, source.records[1].hd_url)
  assert.strictEqual(clean.records[1].thumbnail_url, undefined)
  assert.strictEqual(clean.records[2].thumbnail_url, undefined)
  assert.strictEqual(clean.records[2].can_download_screenshot, false)
  assert.strictEqual(clean.records[2].can_download_report, false)
  assert.strictEqual(clean.records[2].age_bucket, 'unknown')
  assert.strictEqual(clean.records[3].hd_url, undefined)
  var pendingOld = shape.sanitizeSearch(Object.assign({}, source, { outcome: 'miss', unverified_source_count: 1 }))
  assert.strictEqual(pendingOld.outcome, 'unverified')
  assert.deepStrictEqual(pendingOld.records, [])
  assert.deepStrictEqual(pendingOld.evidence_platforms, [])
  var body = await api.searchProgram('真实测试片')
  assert.strictEqual(calls[calls.length - 1].header.Authorization, 'Bearer test-member-token')
  assert.strictEqual(body.records[1].platform, undefined)
  var presented = require('../miniprogram/utils/present').present(body)
  assert.strictEqual(presented.groups[0].rows[0].platform, '真实测试平台')
  assert.strictEqual(presented.groups[0].rows[0].date, '2026-01-02')
  assert.strictEqual(presented.groups[0].rows[0].title, '真实测试片')
  assert(presented.showOpen && presented.showLocked)
  var book = await api.exportBook('真实测试片')
  var data = Buffer.from(book.bytes).toString('utf8')
  assert(data.includes('真实测试平台') && !data.includes('禁止泄露'))
  var def
  global.Page = function (p) { def = p }
  require('../miniprogram/pages/report/index')
  var page = Object.assign({}, def, { data: {}, setData: function (v) { Object.assign(this.data, v) } })
  page.query = { name: '真实测试片' }; page.load(); await drain()
  assert.strictEqual(page.data.phase, 'ready')
  assert(page.data.showDownload)
  nextError = true; page.retry(); await drain()
  assert.strictEqual(page.data.phase, 'fail')
  var toasts = []
  var modals = []
  var opened = []
  wx.showToast = function (opt) { toasts.push(opt) }
  wx.showModal = function (opt) { modals.push(opt) }
  wx.env = { USER_DATA_PATH: '/tmp' }
  wx.openDocument = function (opt) { opened.push(opt) }
  wx.getFileSystemManager = function () {
    return { mkdir: function (opt) { opt.success() }, readdir: function (opt) { opt.success({ files: [] }) }, writeFile: function (opt) { if (opt && opt.success) opt.success() } }
  }
  var reportWxml = require('fs').readFileSync(require('path').join(__dirname, '../miniprogram/pages/report/index.wxml'), 'utf8')
  assert(/<button[^>]*wx:if="{{showDownload}}"[^>]*bindtap="download"[^>]*>下载报告<\/button>/.test(reportWxml))
  assert(/<button\b[^>]*bindtap="download"[^>]*loading="\{\{downloadLoading\}\}"[^>]*disabled="\{\{downloadLoading\}\}"/.test(reportWxml),
    'download button binds loading and disabled')
  var sheet = Object.assign({}, def, {
    data: Object.assign({}, def.data),
    setData: function (v) { Object.assign(this.data, v) }
  })
  sheet.query = { name: '真实测试片' }
  sheet.load()
  await drain()
  assert.strictEqual(sheet.data.phase, 'ready')
  assert(sheet.data.showDownload)
  assert.strictEqual(sheet.data.downloadLoading, false)
  var before = calls.length
  sheet.download()
  assert.strictEqual(sheet.data.downloadLoading, true, 'download button stays loading while exporting')
  sheet.download()
  assert.strictEqual(calls.length, before + 1, 'repeated taps send one export request')
  await drain()
  assert.strictEqual(sheet.data.downloadLoading, false, 'download button recovers after success')
  assert.strictEqual(opened.length, 1)
  assert.strictEqual(modals.length, 0)
  assert.strictEqual(toasts.length, 0)
  before = calls.length
  nextError = true
  sheet.download()
  assert.strictEqual(sheet.data.downloadLoading, true)
  sheet.download()
  sheet.download()
  assert.strictEqual(calls.length, before + 1, 'taps during a failed export send one request')
  await drain()
  assert.strictEqual(sheet.data.downloadLoading, false, 'download button recovers after network failure')
  assert.strictEqual(toasts.length, 0, 'network failure must not toast 网络异常，请重试')
  assert.strictEqual(modals.length, 1)
  var modal = modals[0]
  assert.strictEqual(modal.title, '下载失败')
  assert.strictEqual(modal.content, '网络异常，请重试')
  assert.strictEqual(modal.cancelText, '取消')
  assert.strictEqual(modal.confirmText, '重试')
  assert.strictEqual(modal.confirmColor, '#1D4E9E')
  assert.notStrictEqual(modal.showCancel, false, 'cancel stays on the left')
  modal.success({ confirm: false, cancel: true })
  assert.strictEqual(calls.length, before + 1, 'cancel does not download again')
  assert.strictEqual(sheet.data.downloadLoading, false)
  nextError = true
  modal.success({ confirm: true, cancel: false })
  assert.strictEqual(sheet.data.downloadLoading, true)
  assert.strictEqual(calls.length, before + 2, 'confirm retries the same download')
  sheet.download()
  assert.strictEqual(calls.length, before + 2, 'a second tap during retry sends no extra request')
  await drain()
  assert.strictEqual(modals.length, 2)
  assert.strictEqual(toasts.length, 0)
  assert.strictEqual(sheet.data.downloadLoading, false)
  var savedRequest = wx.request
  wx.request = function (opt) {
    calls.push(opt)
    opt.success({ statusCode: 503, data: {} })
  }
  sheet.download()
  await drain()
  wx.request = savedRequest
  assert.strictEqual(modals.length, 2, 'non-network export errors do not use the download modal')
  assert.strictEqual(toasts.length, 1)
  assert.strictEqual(toasts[0].title, '服务暂不可用')
  assert.strictEqual(toasts[0].icon, 'none')
  assert.strictEqual(sheet.data.downloadLoading, false)
  require('../miniprogram/pages/viewer/index')
  function viewer() {
    return Object.assign({}, def, { data: {}, setData: function (v) { Object.assign(this.data, v) } })
  }
  var shot = viewer()
  shot.onLoad({ name: '真实测试片', id: 'xh1' })
  assert.strictEqual(shot.data.loading, true)
  assert.strictEqual(shot.data.missing, false)
  await drain()
  assert.strictEqual(shot.data.loading, false)
  assert.strictEqual(shot.data.missing, false)
  assert.strictEqual(shot.data.platform, '真实测试平台')
  nextError = true
  shot = viewer(); shot.onLoad({ name: '真实测试片', id: 'xh1' })
  assert.strictEqual(shot.data.loading, true)
  assert.strictEqual(shot.data.missing, false)
  await drain()
  assert.strictEqual(shot.data.loading, false)
  assert.strictEqual(shot.data.missing, false)
  assert.strictEqual(shot.data.failed, true)
  shot.read(); await drain()
  assert.strictEqual(shot.data.failed, false)
  assert.strictEqual(shot.data.missing, false)
  shot = viewer(); shot.onLoad({ name: '真实测试片', id: 'absent' })
  await drain()
  assert.strictEqual(shot.data.loading, false)
  assert.strictEqual(shot.data.missing, true)
  withGenerated = true
  var mixed = await api.searchProgram('真实测试片')
  var view = require('../miniprogram/utils/present').present(mixed)
  assert.deepStrictEqual(view.groups.map(function (g) { return g.platform }), ['真实测试平台', '其他平台'])
  assert.deepStrictEqual(view.evidencePlatforms, ['真实测试平台'])
  var gen = view.groups[1].rows[0]
  assert.strictEqual(gen.date, '2026-07-01')
  assert(!/生成|估算|待核实/.test(JSON.stringify(view)), 'no generated-date wording on P08')
  var mixedBook = await api.exportBook('真实测试片')
  var mixedText = Buffer.from(mixedBook.bytes).toString('utf8')
  var dataRows = (mixedText.match(/<row r="(\d+)"/g) || []).filter(function (r) { return Number(r.match(/\d+/)[0]) >= 6 }).length
  assert.strictEqual(mixedBook.count, 2)
  assert.strictEqual(dataRows, mixedBook.count)
  assert.strictEqual(mixedBook.filename, '汇总-共导出2条.xlsx')
  assert(mixedText.includes('那豆在架 命中结果（共导出 2 条）'))
  assert(!mixedText.includes('2026-07-01'), 'generated-date row is not exported')
  var listed = await api.createQuery(['真实测试片'])
  assert(listed.ok)
  api.advanceQuery(listed.id); await drain()
  assert.strictEqual(api.getQuery(listed.id).items[0].evidencePlatform, '真实测试平台')
  withGenerated = false
  summaryReply = { program_name: '摘要节目', outcome: 'hit', platform_count: 1,
    summary_only_count: 1, summary_hidden_count: 1, platform_groups: [], locked_records: [], evidence_platforms: [],
    summary_unlocks_by_product: { membership_regular: 0, membership_vip: 1, single_program_30d: 1 } }
  var summaryJob = await api.createQuery(['摘要节目'])
  api.advanceQuery(summaryJob.id); await drain()
  assert.strictEqual(api.getQuery(summaryJob.id).items[0].line, '命中 1 条 · 涉及 1 个平台')
  sheet.query = { name: '摘要节目' }
  sheet.load(); await drain()
  assert.strictEqual(sheet.data.header, '命中 1 条 · 涉及 1 个平台')
  assert.strictEqual(sheet.data.summaryHidden, 1)
  assert.deepStrictEqual(sheet.data.summaryPlans.map(p => p.id), ['membership_vip', 'single_program_30d'])
  assert(!sheet.data.summaryPlans.some(p => p.purchasable || p.price))
  assert(sheet.data.summaryPlans[1].pendingPrice)
  assert(reportWxml.includes('summary-mosaic') && reportWxml.includes('summary-padlock'))
  // 单次解锁还没定价：不画按钮，只留「联系客服」。
  assert(!reportWxml.includes('单次解锁待定价') && !reportWxml.includes('singleSummary'))
  assert(reportWxml.includes('session-from="report-summary">联系客服'))
  assert(reportWxml.includes('class="btn card-wide" data-id="{{item.id}}" bindtap="buySummary">选择此方案'))
  sheet.onHide()
  assert(sheet.refreshSummary)
  assert(!sheet.data.showOpen && !sheet.data.showLocked && !sheet.data.showDownload)

  var declaredSummary = { record_id: 'summary1', summary_only: true, access: 'summary', platform: '测试平台',
    network_type: 'IPTV', frame_count: 3, checked_on: '2026-08-01', checked_at: '2026-08-01T01:00:00Z',
    timestamps: ['00:02:14'], thumbnail_url: 'https://img.invalid/restricted', hd_url: 'https://img.invalid/restricted',
    can_download_report: true, can_download_screenshot: true }
  summaryReply.summary_hidden_count = 0
  summaryReply.platform_groups = [{ records: [declaredSummary] }]
  var safe = (await api.searchProgram('摘要节目')).records[0]
  assert.deepStrictEqual(safe, { record_id: 'summary1', access: 'summary', locked: false, summary_only: true,
    platform: '测试平台', network_type: 'IPTV', frame_count: 3, checked_on: '2026-08-01',
    can_download_report: false, can_download_screenshot: false })
  sheet.onShow(); await drain()
  assert.strictEqual(sheet.data.summaryHidden, 0)
  var summaryRow = sheet.data.groups[0].rows[0]
  assert(sheet.data.showOpen && !sheet.data.showLocked && !sheet.data.showDownload && sheet.data.summaryOnlyReport)
  assert.strictEqual(summaryRow.date, '2026-08-01')
  assert.strictEqual(summaryRow.network, 'IPTV')
  assert.strictEqual(summaryRow.frames, '命中 3 帧')
  assert.deepStrictEqual(sheet.data.evidencePlatforms, [])
  var summaryRequests = calls.length
  sheet.openShot({ currentTarget: { dataset: { id: 'summary1' } } })
  sheet.download()
  assert.strictEqual(calls.length, summaryRequests)
  shot = viewer(); shot.onLoad({ name: '摘要节目', id: 'summary1' }); await drain()
  assert(shot.data.missing && !shot.data.canSave && !shot.data.image && !shot.data.downloadEntry)
  summaryReply.platform_groups[0].records.push({ record_id: 'normal', access: 'full', can_download_report: true })
  sheet.load(); await drain()
  assert.strictEqual(sheet.data.header, '命中 2 条 · 涉及 1 个平台')
  assert(sheet.data.showDownload && !sheet.data.summaryOnlyReport)
  summaryReply = null
  pendingVerification = true
  var pendingJob = await api.createQuery(['待核验测试'])
  api.advanceQuery(pendingJob.id); await drain()
  var pending = api.getQuery(pendingJob.id)
  assert.strictEqual(pending.items[0].badge, '待核验')
  assert(!pending.items[0].hit && !pending.items[0].missed && !pending.items[0].failed)
  assert(pending.head.includes('命中 0｜未命中 0') && pending.head.includes('待核验 1'))
  sheet.query = { name: '待核验测试' }
  sheet.load(); await drain()
  assert.strictEqual(sheet.data.phase, 'unverified')
  assert(!sheet.data.showDownload && !sheet.data.showLocked && !sheet.data.showEvidenceLink)
  var initialRequests = calls.length
  sheet.download(); sheet.buySingle(); sheet.buyMember(); sheet.applyEvidence()
  assert.strictEqual(calls.length, initialRequests)
  assert.deepStrictEqual(await api.exportBook('待核验测试'), { ok: false, message: '数据待核验' })
  config.apiBase = ''
  await assert.rejects(api.searchProgram('真实测试片'), /未配置服务地址/)
  assert.strictEqual(state.userId(), 'member')
  console.log('hybrid API, identity, retry, report, export count and generated-date exclusion checks ok')
}
main().catch(function (error) { console.error(error); process.exitCode = 1 })

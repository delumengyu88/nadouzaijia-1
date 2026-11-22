var assert = require('assert')
var config = require('../miniprogram/config')
config.calibrating = false
assert.strictEqual(config.mode, 'hybrid')
assert.strictEqual(config.apiBase, '')
config.apiBase = '/test-api'
var calls = []
var nextError = false
var withGenerated = false
var store = {}
global.wx = {
  getStorageSync: function (k) { return store[k] }, setStorageSync: function (k, v) { store[k] = v },
  getSystemInfoSync: function () { return { statusBarHeight: 20 } },
  request: function (opt) {
    calls.push(opt)
    if (nextError) { nextError = false; opt.fail(); return }
    if (withGenerated) {
      // Server shape after generated dates: 其他平台 group arrives first here on purpose,
      // the generated-date row cannot be exported, and evidence_platforms omits it.
      opt.success({ statusCode: 200, data: { program_name: opt.data.name, outcome: 'hit', platform_count: 1,
        evidence_platforms: ['真实测试平台'], evidence_program_name: opt.data.name,
        platform_groups: [
          { platform: '其他平台', records: [{ record_id: 'gen1', locked: false, platform: '其他平台', access: 'full',
            checked_at: '2026-07-01T12:00:00+08:00', can_download_report: false }] },
          { platform: '真实测试平台', records: [{ record_id: 'xh1', locked: false, platform: '真实测试平台', access: 'full',
            checked_at: '2026-01-02T03:04:05+08:00', can_download_report: true },
          { record_id: 'xh2', locked: false, platform: '真实测试平台', access: 'full',
            checked_at: '2026-01-03T03:04:05+08:00', can_download_report: true }] }],
        locked_records: [{ record_id: 'locked', locked: true, age_bucket: 'd30_89' }],
        locked_summary: { locked_total: 1, unlockable_total: 1, by_required_tier: { vip: 1 }, cheapest_full_unlock: 'single_program_30d' }
      } })
      return
    }
    opt.success({ statusCode: 200, data: { program_name: opt.data.name, outcome: 'hit', platform_count: 1, evidence_platforms: ['真实测试平台'], evidence_program_name: opt.data.name,
      platform_groups: [{ records: [{ record_id: 'xh1', locked: false, platform: '真实测试平台', category: '测试类别',
        access: 'full', checked_at: '2026-01-02T03:04:05+08:00', can_download_report: true }] }],
      locked_records: [{ record_id: 'locked', locked: true, age_bucket: 'd30_89', platform: '禁止泄露' }],
      locked_summary: { locked_total: 1, unlockable_total: 1, by_required_tier: { vip: 1 }, cheapest_full_unlock: 'single_program_30d' }
    } })
  }
}
var api = require('../miniprogram/api')
var state = require('../miniprogram/api/state')
function drain() { return new Promise(function (resolve) { setImmediate(resolve) }) }
async function main() {
  api.reset(); state.setSession({ token: 'test-free-token', expires_at: new Date(Date.now() + 86400000).toISOString(), user: { id: 'free', tier: 'free' } })
  assert.strictEqual(api.isLive, true)
  assert.strictEqual(api.listEvidence().length, 0)
  var query = api.createQuery(['真实测试片', '失败测试片'])
  api.advanceQuery(query.id); await drain()
  assert.strictEqual(calls[0].url, '/test-api/api/v1/programs/search')
  assert.strictEqual(calls[0].header.Authorization, 'Bearer test-free-token')
  nextError = true; api.advanceQuery(query.id); await drain()
  var results = api.getQuery(query.id)
  assert(results.networkError && results.done && results.items[0].hit && results.items[1].failed)
  assert.strictEqual(results.failBanner, '网络异常，请重试')
  assert.strictEqual(results.head, '已完成 1 / 2')
  assert.strictEqual(results.percent, 50)
  assert(results.showNetworkRetry)
  assert.strictEqual(calls.length, 2, 'network failure must not start another search')
  assert.strictEqual(results.items[0].evidencePlatform, '真实测试平台')
  api.retryItem(query.id, '失败测试片'); await drain()
  assert(api.getQuery(query.id).items[1].hit)
  state.setSession({ token: 'test-member-token', expires_at: new Date(Date.now() + 86400000).toISOString(), user: { id: 'member', tier: 'vip' } })
  assert.strictEqual(api.getQuery(query.id), null)
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
  assert.strictEqual(shot.data.missing, true)
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
  var listed = api.createQuery(['真实测试片'])
  api.advanceQuery(listed.id); await drain()
  assert.strictEqual(api.getQuery(listed.id).items[0].evidencePlatform, '真实测试平台')
  withGenerated = false
  config.apiBase = ''
  await assert.rejects(api.searchProgram('真实测试片'), /未配置服务地址/)
  assert.strictEqual(state.userId(), 'member')
  console.log('hybrid API, identity, retry, report, export count and generated-date exclusion checks ok')
}
main().catch(function (error) { console.error(error); process.exitCode = 1 })

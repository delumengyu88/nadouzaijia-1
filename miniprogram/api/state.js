var date = require('../utils/date')

var KEY = 'nadou.session.v1'
var memory = null

function blankAccount() {
  return {
    inviteBound: false,
    inviteCode: '',
    inviteExpiry: '',
    paidVipExpiry: '',
    paidRegularExpiry: '',
    paidRegularResume: '',
    programs: [],
    orders: [],
    queries: [],
    evidence: [],
    misses: 0,
    missLockUntil: 0
  }
}

function futureOr(fixed, fallbackDays) {
  var day = date.today()
  if (fixed > day) return fixed
  return date.addDays(day, fallbackDays)
}

function buildSeed() {
  var member = blankAccount()
  member.inviteBound = true
  member.inviteCode = 'A8K3N2P7'
  member.inviteExpiry = futureOr('2026-12-22', 31)
  member.paidVipExpiry = futureOr('2026-12-18', 27)
  member.programs = [
    { name: '雾港来信', tier: 'program_30', expiry: date.addDays(date.today(), 6) }
  ]
  member.orders = [
    order('DD20261122001', 'membership_vip', 599, 'granted', '2026-11-22', ''),
    order('DD20261122002', 'membership_vip', 599, 'granting', '2026-11-22', ''),
    order('DD20261121003', 'membership_regular', 299, 'confirming', '2026-11-21', ''),
    order('DD20261120004', 'single_program_30d', 199, 'pending', '2026-11-20', '星河旧梦'),
    order('DD20261119005', 'membership_vip', 599, 'failed', '2026-11-19', ''),
    order('DD20261118006', 'membership_regular', 299, 'cancelled', '2026-11-18', ''),
    order('DD20261122018', 'membership_vip', 599, 'grant_error', '2026-11-22', '')
  ]
  member.orders[4].unpaidConfirmed = false
  member.queries = [
    {
      id: 'qseed',
      created: '2026-11-20',
      status: 'done',
      seen: false,
      progress: 4,
      total: 4,
      items: [
        { name: '星河旧梦', error: false },
        { name: '长街烟火', error: false },
        { name: '雾港来信', error: false },
        { name: '南岭追光', error: true }
      ]
    },
    {
      id: 'qfail',
      created: '2026-11-18',
      status: 'done',
      seen: true,
      progress: 2,
      total: 2,
      items: [
        { name: '星河旧梦', error: true },
        { name: '南岭追光', error: true }
      ]
    },
    {
      id: 'qmiss',
      created: '2026-11-18',
      status: 'done',
      seen: true,
      progress: 2,
      total: 2,
      items: [
        { name: '无名甲', error: false },
        { name: '无名乙', error: false }
      ]
    }
  ]
  member.evidence = seedEvidence()
  return {
    splashSeen: false,
    announceSeen: false,
    draft: '',
    seq: 20,
    evidenceSeq: 20,
    current: 'guest',
    accounts: {
      free: blankAccount(),
      member: member
    }
  }
}

function order(no, product, price, status, day, programName) {
  return {
    no: no,
    product: product,
    price: price,
    status: status,
    day: day,
    programName: programName || '',
    unpaidConfirmed: status === 'cancelled' || status === 'pending'
  }
}

function seedEvidence() {
  var day = '2026-11-20'
  var base = function (id, status) {
    return {
      id: id,
      no: 'QZ2026112201' + id,
      name: '雾港来信',
      kind: 'normal',
      status: status,
      day: day,
      contact: '林可',
      phone: '13800002610',
      note: '请尽量取近期播出记录。',
      org: '',
      reason: '',
      refundAmount: 0,
      refundMethod: '',
      refundTime: ''
    }
  }
  var rows = [
    base('1', 'submitted'),
    base('2', 'accepted'),
    base('3', 'pay'),
    base('4', 'working'),
    base('5', 'delivered'),
    base('6', 'done'),
    base('7', 'failed'),
    base('8', 'closed'),
    base('9', 'cancelled')
  ]
  rows[6].reason = '失败原因：目标平台当期无可用片源。'
  rows[6].refundAmount = 680
  rows[6].refundMethod = '线下原路退回'
  rows[6].refundTime = '2026-11-21 16:30'
  return rows
}

function storageGet() {
  if (typeof wx === 'undefined' || !wx.getStorageSync) return null
  try {
    return wx.getStorageSync(KEY) || null
  } catch (err) {
    return null
  }
}

function storageSet(value) {
  if (typeof wx === 'undefined' || !wx.setStorageSync) return
  try {
    wx.setStorageSync(KEY, value)
  } catch (err) {}
}

function load() {
  if (memory) return memory
  var saved = storageGet()
  if (saved && saved.accounts && saved.accounts.free && saved.accounts.member) {
    memory = saved
    return memory
  }
  memory = buildSeed()
  storageSet(memory)
  return memory
}

function save() {
  if (memory) storageSet(memory)
}

function reset() {
  memory = buildSeed()
  save()
  return memory
}

function currentId() {
  return load().current
}

function account() {
  var state = load()
  if (state.current === 'guest') return null
  return state.accounts[state.current]
}

function userId() {
  var id = currentId()
  if (id === 'member') return 'u-member'
  if (id === 'free') return 'u-free'
  return ''
}

module.exports = {
  load: load,
  save: save,
  reset: reset,
  account: account,
  currentId: currentId,
  userId: userId,
  blankAccount: blankAccount
}

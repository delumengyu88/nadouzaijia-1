Component({
  data: {
    selected: 0,
    list: [
      { pagePath: '/pages/home/index', text: '首页', icon: '/assets/tab-home.png', iconOn: '/assets/tab-home-on.png' },
      { pagePath: '/pages/member/index', text: '会员中心', icon: '/assets/tab-member.png', iconOn: '/assets/tab-member-on.png' },
      { pagePath: '/pages/profile/index', text: '个人中心', icon: '/assets/tab-profile.png', iconOn: '/assets/tab-profile-on.png' }
    ]
  },
  methods: {
    switchTab: function (e) {
      var path = e.currentTarget.dataset.path
      var index = Number(e.currentTarget.dataset.index)
      this.setData({ selected: index })
      wx.switchTab({ url: path })
    }
  }
})

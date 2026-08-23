Component({
  data: {
    selected: 0,
    inboxCount: "",
    list: [
      { pagePath: "/pages/home/index", text: "首页" },
      { pagePath: "/pages/schedule/index", text: "对阵" },
      { pagePath: "/pages/standings/index", text: "排名" },
      { pagePath: "/pages/data/index", text: "数据" },
      { pagePath: "/pages/mine/index", text: "我的" }
    ]
  },
  lifetimes: {
    attached() {
      this.syncSelected()
    }
  },
  pageLifetimes: {
    show() {
      this.syncSelected()
    }
  },
  methods: {
    syncSelected() {
      const pages = getCurrentPages()
      const current = pages[pages.length - 1]
      if (!current) return
      const currentPath = `/${current.route}`
      const selected = this.data.list.findIndex((item) => item.pagePath === currentPath)
      if (selected >= 0 && selected !== this.data.selected) {
        this.setData({ selected })
      }
    },
    switchTab(event) {
      const { index, path } = event.currentTarget.dataset
      const pages = getCurrentPages()
      const current = pages[pages.length - 1]
      if (this.switching || (current && `/${current.route}` === path)) return
      const previous = this.data.selected
      this.switching = true
      this.setData({ selected: Number(index), switching: true })
      wx.switchTab({
        url: path,
        fail: () => this.setData({ selected: previous }),
        complete: () => {
          this.switching = false
          this.setData({ switching: false })
        }
      })
    }
  }
})

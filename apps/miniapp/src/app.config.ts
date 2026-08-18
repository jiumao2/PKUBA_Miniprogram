export default defineAppConfig({
  pages: [
    "pages/home/index",
    "pages/schedule/index",
    "pages/standings/index",
    "pages/data/index",
    "pages/mine/index",
  ],
  window: {
    backgroundTextStyle: "light",
    navigationBarBackgroundColor: "#faf9f6",
    navigationBarTitleText: "PKUBA",
    navigationBarTextStyle: "black",
    backgroundColor: "#f2f0eb",
  },
  tabBar: {
    color: "#76716a",
    selectedColor: "#c91f26",
    backgroundColor: "#faf9f6",
    borderStyle: "black",
    list: [
      { pagePath: "pages/home/index", text: "首页" },
      { pagePath: "pages/schedule/index", text: "赛程" },
      { pagePath: "pages/standings/index", text: "排名" },
      { pagePath: "pages/data/index", text: "数据" },
      { pagePath: "pages/mine/index", text: "我的" },
    ],
  },
});

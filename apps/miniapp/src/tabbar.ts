import Taro from "@tarojs/taro";

interface TabBarController {
  setData(data: { selected: number }): void;
}

interface PageWithTabBar {
  getTabBar?: () => TabBarController | null;
}

export function syncTabBar(selected: number) {
  const apply = () => {
    const page = Taro.getCurrentInstance().page as unknown as PageWithTabBar | undefined;
    page?.getTabBar?.()?.setData({ selected });
  };
  apply();
  setTimeout(apply, 0);
}

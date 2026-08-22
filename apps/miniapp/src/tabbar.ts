import Taro from "@tarojs/taro";

import { api } from "./api";
import { getMiniAppSession } from "./auth";

interface TabBarController {
  setData(data: { selected?: number; inboxCount?: string }): void;
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
  void refreshInboxBadge();
}

export async function refreshInboxBadge() {
  const apply = (inboxCount: string) => {
    const page = Taro.getCurrentInstance().page as unknown as PageWithTabBar | undefined;
    page?.getTabBar?.()?.setData({ inboxCount });
  };
  const token = getMiniAppSession();
  if (!token) {
    apply("");
    return;
  }
  try {
    const summary = await api.getInboxSummary(token);
    apply(summary.open_count > 0 ? summary.display_count : "");
  } catch {
    apply("");
  }
}

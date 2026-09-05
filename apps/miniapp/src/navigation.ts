import Taro from "@tarojs/taro";

let navigating = false;
export type ScheduleFocusIntent = { date: string; id: number };

let scheduleFocusIntent: ScheduleFocusIntent | null = null;
let scheduleFocusIntentId = 0;

export async function navigateToOnce(url: string) {
  if (navigating) return false;
  navigating = true;
  try {
    await Taro.navigateTo({ url });
    return true;
  } finally {
    setTimeout(() => {
      navigating = false;
    }, 250);
  }
}

export async function switchToScheduleDate(date: string) {
  const intent = { date, id: ++scheduleFocusIntentId };
  scheduleFocusIntent = intent;
  try {
    await Taro.switchTab({ url: "/pages/schedule/index" });
    return true;
  } catch (reason) {
    if (scheduleFocusIntent === intent) scheduleFocusIntent = null;
    throw reason;
  }
}

export function consumeScheduleFocusIntent() {
  const intent = scheduleFocusIntent;
  scheduleFocusIntent = null;
  return intent;
}

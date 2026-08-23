import Taro from "@tarojs/taro";

let navigating = false;

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

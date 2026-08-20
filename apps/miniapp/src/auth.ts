import type { WeChatExchange } from "@pkuba/api-client";
import Taro from "@tarojs/taro";

import { api } from "./api";

export const MINIAPP_SESSION_KEY = "pkuba_miniapp_session";

export function getMiniAppSession(): string {
  return Taro.getStorageSync<string>(MINIAPP_SESSION_KEY) || "";
}

export function saveMiniAppSession(token: string) {
  Taro.setStorageSync(MINIAPP_SESSION_KEY, token);
}

export function clearMiniAppSession() {
  Taro.removeStorageSync(MINIAPP_SESSION_KEY);
}

export async function exchangeCurrentWeChat(): Promise<WeChatExchange> {
  const login = await Taro.login();
  const exchanged = await api.exchangeWeChat(login.code);
  if (exchanged.session_token) saveMiniAppSession(exchanged.session_token);
  return exchanged;
}

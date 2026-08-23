import type { WeChatExchange } from "@pkuba/api-client";
import Taro from "@tarojs/taro";

import { api } from "./api";
import {
  resolveMiniAppIdentityWith,
  type MiniAppIdentityAdapter,
  type ResolvedMiniAppIdentity,
} from "./identity";

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

const defaultIdentityAdapter: MiniAppIdentityAdapter = {
  readToken: getMiniAppSession,
  clearToken: clearMiniAppSession,
  getMe: (token) => api.getMiniAppMe(token),
  exchange: exchangeCurrentWeChat,
};

/**
 * Restore the current WeChat identity without presenting a login surface.
 *
 * Public pages use this to reveal the same private controls an already
 * registered leader or administrator would see after entering from “我的”.
 * Unknown OpenIDs remain anonymous and are never taken into profile setup.
 */
export async function resolveMiniAppIdentity(
  adapter: MiniAppIdentityAdapter = defaultIdentityAdapter,
): Promise<ResolvedMiniAppIdentity> {
  return resolveMiniAppIdentityWith(adapter);
}

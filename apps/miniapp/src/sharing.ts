import Taro, { useShareAppMessage, useShareTimeline } from "@tarojs/taro";
import { useEffect } from "react";

export interface PublicShareOptions {
  title: string;
  path: string;
  query?: string;
}

export function gameShareOptions(
  gameId: string,
  title = "PKUBA 比赛详情",
): PublicShareOptions {
  const encodedGameId = encodeURIComponent(gameId);
  return {
    title,
    path: `/pages/game-media/index?id=${encodedGameId}`,
    query: `id=${encodedGameId}`,
  };
}

export function usePublicPageShare(options: PublicShareOptions) {
  useShareAppMessage(() => ({ title: options.title, path: options.path }));
  useShareTimeline(() => ({ title: options.title, query: options.query }));

  useEffect(() => {
    void Taro.showShareMenu({
      showShareItems: ["shareAppMessage", "shareTimeline"],
    }).catch(() => undefined);
  }, []);
}

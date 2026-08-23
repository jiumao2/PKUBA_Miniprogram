export function gameDetailRoute(gameId: string) {
  return `/pages/game-media/index?id=${encodeURIComponent(gameId)}`;
}

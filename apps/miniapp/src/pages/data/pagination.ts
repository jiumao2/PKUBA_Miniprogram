export const PLAYER_PAGE_SIZE = 20;
export const PLAYER_MAX_PAGES = 5;
export const PLAYER_MAX_ROWS = PLAYER_PAGE_SIZE * PLAYER_MAX_PAGES;

export interface LeaderboardPage<T> {
  page: number;
  page_size: number;
  total: number;
  items: T[];
}

export function playerPageCount(total: number) {
  return Math.min(PLAYER_MAX_PAGES, Math.ceil(Math.max(0, total) / PLAYER_PAGE_SIZE));
}

export function playerVisibleTotal(total: number) {
  return Math.min(PLAYER_MAX_ROWS, Math.max(0, total));
}

export async function loadCompleteList<T>(
  loadPage: (page: number, pageSize: number) => Promise<LeaderboardPage<T>>,
  keyOf: (item: T) => string,
  pageSize = 100,
) {
  const first = await loadPage(1, pageSize);
  const pageCount = Math.ceil(first.total / pageSize);
  const pages = [first];
  for (let page = 2; page <= pageCount; page += 1) {
    pages.push(await loadPage(page, pageSize));
  }

  const seen = new Set<string>();
  return pages.flatMap((result) => result.items).filter((item) => {
    const key = keyOf(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

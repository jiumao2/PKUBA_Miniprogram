import { describe, expect, it, vi } from "vitest";

import {
  loadCompleteList,
  PLAYER_MAX_ROWS,
  PLAYER_PAGE_SIZE,
  playerPageCount,
  playerVisibleTotal,
} from "./pagination";

describe("data page pagination", () => {
  it("limits player leaderboards to five pages of twenty", () => {
    expect(PLAYER_PAGE_SIZE).toBe(20);
    expect(PLAYER_MAX_ROWS).toBe(100);
    expect(playerPageCount(0)).toBe(0);
    expect(playerPageCount(19)).toBe(1);
    expect(playerPageCount(100)).toBe(5);
    expect(playerPageCount(276)).toBe(5);
    expect(playerVisibleTotal(276)).toBe(100);
  });

  it("loads every team page and removes duplicate rows defensively", async () => {
    const loadPage = vi.fn(async (page: number, pageSize: number) => ({
      page,
      page_size: pageSize,
      total: 205,
      items: page === 1
        ? Array.from({ length: 100 }, (_, index) => ({ id: String(index + 1) }))
        : page === 2
          ? Array.from({ length: 100 }, (_, index) => ({ id: String(index + 101) }))
          : [{ id: "200" }, ...Array.from({ length: 5 }, (_, index) => ({ id: String(index + 201) }))],
    }));

    const rows = await loadCompleteList(loadPage, (item) => item.id);

    expect(loadPage).toHaveBeenCalledTimes(3);
    expect(rows).toHaveLength(205);
    expect(rows[rows.length - 1]?.id).toBe("205");
  });
});

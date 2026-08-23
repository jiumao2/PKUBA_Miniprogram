import { describe, expect, it } from "vitest";

import { gameDetailRoute } from "./routes";

describe("miniapp routes", () => {
  it("uses one canonical game detail route for every entry surface", () => {
    expect(gameDetailRoute("game/id"))
      .toBe("/pages/game-media/index?id=game%2Fid");
  });
});

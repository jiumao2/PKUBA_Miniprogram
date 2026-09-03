import { describe, expect, it } from "vitest";

import { gameHeadingScore } from "./viewModel";

describe("independent game detail heading", () => {
  it("keeps the only no-score VS marker while official results show the score", () => {
    expect(gameHeadingScore(null, null)).toBe("VS");
    expect(gameHeadingScore(78, 66)).toBe("78:66");
  });
});

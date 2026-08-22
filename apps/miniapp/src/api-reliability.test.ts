import { describe, expect, it } from "vitest";

import {
  createPkubaClient,
  type RequestAdapter,
  type RequestOptions,
} from "@pkuba/api-client";

describe("API reliability client", () => {
  it("collects every public schedule page without dropping filters", async () => {
    const urls: string[] = [];
    const request: RequestAdapter = async <T>(
      url: string,
      _options?: RequestOptions,
    ) => {
      urls.push(url);
      const parsed = new URL(url, "https://pkuba.invalid");
      const page = Number(parsed.searchParams.get("page"));
      const data = {
        items: [{ id: page === 1 ? "game-1" : "game-2" }],
        total: 2,
        page,
        page_size: 100,
      };
      return { status: 200, data: data as T };
    };

    const games = await createPkubaClient("", request).getGames("?division_id=division-1");

    expect(games.map((game) => game.id)).toEqual(["game-1", "game-2"]);
    expect(urls).toHaveLength(2);
    expect(urls[0]).toContain("division_id=division-1");
    expect(urls[0]).toContain("page=1");
    expect(urls[1]).toContain("page=2");
  });

  it("sends the caller supplied idempotency key on a reschedule command", async () => {
    let capturedOptions: RequestOptions | undefined;
    const request: RequestAdapter = async <T>(
      _url: string,
      options?: RequestOptions,
    ) => {
      capturedOptions = options;
      return { status: 201, data: { id: "request-1" } as T };
    };
    const client = createPkubaClient("", request);

    await client.createRescheduleRequest(
      {
        game_id: "00000000-0000-0000-0000-000000000001",
        expected_game_version: 3,
        target_date: "2026-04-18",
        target_period_id: "00000000-0000-0000-0000-000000000002",
      },
      "miniapp-session",
      "reschedule-retry-key",
    );

    expect(capturedOptions?.headers?.["Idempotency-Key"]).toBe("reschedule-retry-key");
    expect(capturedOptions?.headers?.Authorization).toBe("Bearer miniapp-session");
  });
});

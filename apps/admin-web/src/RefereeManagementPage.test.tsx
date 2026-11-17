import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AdminSeason, RefereeGame, createAdminClient } from "@pkuba/api-client";

import { RefereeManagementPage } from "./RefereeManagementPage";

afterEach(cleanup);
const roles = ["CREW_CHIEF", "FIRST_UMPIRE", "SECOND_UMPIRE", "SCORER", "TIMER"];
const season = { id: "season-1", name: "测试赛季", status: "PUBLISHED", year: 2026 } as AdminSeason;
const match = {
  id: "game-1", version: 1, code: "G001", date: "2026-11-01", period_id: "p1", period_name: "午间",
  start_time: "12:50:00", venue_name: "测试球场", home: "甲队", away: "乙队", status: "SCHEDULED",
  stage: "GROUP", roles, assignments: [],
} as RefereeGame;

function setup(game = match) {
  const client = {
    listRefereeQualifications: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 }),
    listRefereeGames: vi.fn().mockResolvedValue({ items: [game], total: 1, page: 1, page_size: 20 }),
    listRefereeCandidates: vi.fn().mockResolvedValue({ items: [
      { referee_id: "person-1", username: "张裁判", available: true, conflict_game_code: null },
      { referee_id: "person-2", username: "李裁判", available: false, conflict_game_code: "G002" },
    ], total: 2, page: 1, page_size: 100 }),
    assignReferees: vi.fn().mockResolvedValue(game),
    setRefereeQualification: vi.fn().mockResolvedValue({}),
  };
  render(<RefereeManagementPage client={client as unknown as ReturnType<typeof createAdminClient>}
    seasons={[season]} seasonId={season.id} onSeasonChange={vi.fn()} />);
  return client;
}

async function openEditor() {
  fireEvent.click(await screen.findByText("选派 / 调整人员"));
  await waitFor(() => expect(screen.getByLabelText("主裁判")).toBeEnabled());
}

describe("裁判与记录台选派", () => {
  it("非决赛隐藏两个额外岗位，并按岗位提交选派", async () => {
    const client = setup();
    await openEditor();
    expect(screen.queryByLabelText("助理记录员")).toBeNull();
    expect(screen.queryByLabelText("进攻计时员")).toBeNull();
    fireEvent.change(screen.getByLabelText("主裁判"), { target: { value: "person-1" } });
    fireEvent.click(screen.getByText("保存选派"));
    await waitFor(() => expect(client.assignReferees).toHaveBeenCalledOnce());
    expect(client.assignReferees.mock.calls[0].slice(0, 3)).toEqual([
      "season-1", "game-1", { expected_version: 1, assignments: [{ role: "CREW_CHIEF", referee_id: "person-1" }] },
    ]);
  });

  it("决赛展示全部七个岗位", async () => {
    setup({ ...match, stage: "FINAL", roles: [...roles, "ASSISTANT_SCORER", "SHOT_CLOCK_OPERATOR"] });
    await openEditor();
    expect(screen.getByLabelText("助理记录员")).toBeInTheDocument();
    expect(screen.getByLabelText("进攻计时员")).toBeInTheDocument();
  });

  it("网络结果不明时锁住编辑，并用原请求核对", async () => {
    const client = setup();
    client.assignReferees.mockRejectedValueOnce(new TypeError("网络中断"));
    await openEditor();
    fireEvent.change(screen.getByLabelText("记录员"), { target: { value: "person-1" } });
    fireEvent.click(screen.getByText("保存选派"));
    const retry = await screen.findByText("核对上次操作");
    expect(screen.getByLabelText("记录员")).toBeDisabled();
    fireEvent.click(retry);
    await waitFor(() => expect(client.assignReferees).toHaveBeenCalledTimes(2));
    expect(client.assignReferees.mock.calls[1]).toEqual(client.assignReferees.mock.calls[0]);
  });

  it("冲突人员不可选，已选人员不能兼任其他岗位", async () => {
    setup();
    await openEditor();
    const chief = screen.getByLabelText("主裁判") as HTMLSelectElement;
    expect(chief.querySelector('option[value="person-2"]')).toBeDisabled();
    fireEvent.change(chief, { target: { value: "person-1" } });
    expect(screen.getByLabelText("记录员").querySelector('option[value="person-1"]')).toBeDisabled();
  });
});

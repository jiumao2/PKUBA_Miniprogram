import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type AdminSeason, type Registration, type createAdminClient } from "@pkuba/api-client";
import { RegistrationManagementPage } from "./RegistrationManagementPage";

afterEach(cleanup);
const season = { id: "s1", name: "合成赛季", status: "PUBLISHED", divisions: [{ id: "d1", name: "测试组" }] } as AdminSeason;
const registration = { id: "r1", season_id: "s1", team_name: "合成球队", team_id: null, version: 2, status: "PENDING",
  form: { team_name: "合成球队", uniform_color: "白", leader_name: "测试领队", phone: "10000000000", email: "local@example.invalid",
    players: [{ id: "", name: "测试队员", college: "测试学院", student_number: "001234", jersey_number: "00" }] }, approved_form: null } as Registration;
function setup() {
  const client = {
    getRegistrationPolicy: vi.fn().mockResolvedValue({ deadline: "2099-01-01T10:00:00Z", version: 1, open: true }),
    listRegistrations: vi.fn().mockResolvedValue({ items: [registration], total: 1, page: 1, page_size: 20 }),
    getRegistration: vi.fn().mockResolvedValue(registration),
    getRegistrationHistory: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 10 }),
    reviewRegistration: vi.fn().mockResolvedValue(registration), setRegistrationPolicy: vi.fn().mockResolvedValue({}),
  };
  render(<RegistrationManagementPage client={client as unknown as ReturnType<typeof createAdminClient>} season={season} seasons={[season]} onSeasonChange={vi.fn()} />);
  return client;
}
async function open() { fireEvent.click(await screen.findByText("查看报名")); await screen.findByText("001234"); }
describe("球队报名审核", () => {
  it("读取私密报名，二次确认后才通过并携带组别和版本", async () => {
    const client = setup(); await open();
    fireEvent.click(screen.getByText("通过报名"));
    expect(client.reviewRegistration).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("确认通过"));
    await waitFor(() => expect(client.reviewRegistration).toHaveBeenCalledOnce());
    expect(client.reviewRegistration.mock.calls[0].slice(0, 2)).toEqual(["r1", { expected_version: 2, approved: true, reason: "", division_id: "d1" }]);
  });
  it("退回需要意见，保存结果不明时用原请求重试", async () => {
    const client = setup(); client.reviewRegistration.mockRejectedValueOnce(new TypeError("网络中断")); await open();
    expect(screen.getByText("退回修改")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("审核意见"), { target: { value: "请核对学号" } });
    fireEvent.click(screen.getByText("退回修改")); fireEvent.click(screen.getByText("确认退回"));
    fireEvent.click(await screen.findByText("核对上次操作"));
    await waitFor(() => expect(client.reviewRegistration).toHaveBeenCalledTimes(2));
    expect(client.reviewRegistration.mock.calls[1]).toEqual(client.reviewRegistration.mock.calls[0]);
  });
  it("详情读取失败时不保留旧报名内容", async () => {
    const client = setup(); client.getRegistration.mockRejectedValue(new Error("权限已变化"));
    fireEvent.click(await screen.findByText("查看报名"));
    await screen.findByText("权限已变化"); expect(screen.queryByText("001234")).toBeNull();
  });
});

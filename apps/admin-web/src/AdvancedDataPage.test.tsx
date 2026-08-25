import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AdvancedModel, createAdminClient } from "@pkuba/api-client";

import { AdvancedDataPage } from "./AdvancedDataPage";

type AdminClient = ReturnType<typeof createAdminClient>;

const models: AdvancedModel[] = [
  {
    key: "accounts",
    label: "账号",
    model_name: "Account",
    mutation_mode: "READ_ONLY",
    immutable: true,
    fields: [
      { name: "id", type: "UUIDField", relation: false, nullable: false, sensitive: false },
      { name: "password", type: "CharField", relation: false, nullable: false, sensitive: true },
      { name: "publication", type: "ForeignKey", relation: true, nullable: true, sensitive: false },
      { name: "status", type: "CharField", relation: false, nullable: false, sensitive: false },
      { name: "username", type: "CharField", relation: false, nullable: false, sensitive: false },
      { name: "token_hash", type: "CharField", relation: false, nullable: false, sensitive: true },
    ],
  },
];

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AdvancedDataPage", () => {
  it("shows complete sensitive fields but keeps workflow models read only", async () => {
    const client = {
      listAdvancedModels: vi.fn().mockResolvedValue(models),
      listAdvancedRecords: vi.fn().mockResolvedValue({
        model: "accounts",
        label: "账号",
        mutation_mode: "READ_ONLY",
        total: 1,
        offset: 0,
        limit: 50,
        search: "",
        sort: "",
        direction: "desc",
        items: [
          {
            id: "10000000-0000-0000-0000-000000000001",
            model: "account",
            values: {
              id: "10000000-0000-0000-0000-000000000001",
              username: "core-developer",
              status: "ACTIVE",
              publication: "20000000-0000-0000-0000-000000000001",
              password: "pbkdf2_sha256$full-hash",
              token_hash: "opaque-token-digest",
            },
          },
        ],
      }),
    } as unknown as AdminClient;
    const user = userEvent.setup();
    render(<AdvancedDataPage client={client} />);

    expect(await screen.findByText("core-developer")).toBeTruthy();
    expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
      "status",
      "username",
      "publication",
      "id",
      "password",
      "token_hash",
    ]);
    await user.click(screen.getByText("core-developer"));
    expect(screen.getAllByText("pbkdf2_sha256$full-hash")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "新建" })).toBeNull();
    expect(screen.queryByRole("button", { name: "编辑" })).toBeNull();
  });

  it("uses server-wide search and pagination instead of filtering the first page", async () => {
    const listAdvancedRecords = vi.fn()
      .mockResolvedValueOnce({
        model: "accounts", label: "账号", mutation_mode: "READ_ONLY",
        total: 101, offset: 0, limit: 50, search: "", sort: "", direction: "desc",
        items: [{ id: "first", model: "account", values: { username: "first-page", status: "ACTIVE" } }],
      })
      .mockResolvedValueOnce({
        model: "accounts", label: "账号", mutation_mode: "READ_ONLY",
        total: 101, offset: 0, limit: 50, search: "target", sort: "", direction: "desc",
        items: [{ id: "target", model: "account", values: { username: "target-user", status: "ACTIVE" } }],
      })
      .mockResolvedValueOnce({
        model: "accounts", label: "账号", mutation_mode: "READ_ONLY",
        total: 101, offset: 50, limit: 50, search: "target", sort: "", direction: "desc", items: [],
      });
    const client = {
      listAdvancedModels: vi.fn().mockResolvedValue(models),
      listAdvancedRecords,
    } as unknown as AdminClient;
    const user = userEvent.setup();
    render(<AdvancedDataPage client={client} />);

    expect(await screen.findByText("first-page")).toBeTruthy();
    await user.type(screen.getByLabelText("搜索全部记录"), "target");
    await user.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("target-user")).toBeTruthy();
    expect(listAdvancedRecords).toHaveBeenLastCalledWith(
      "accounts", 0, 50, { search: "target", sort: "", direction: "desc" },
    );

    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(listAdvancedRecords).toHaveBeenLastCalledWith(
      "accounts", 50, 50, { search: "target", sort: "", direction: "desc" },
    );
  });
});

// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { ScoresheetGameContextReview } from "@pkuba/scoresheet-domain";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("@tarojs/components", () => ({
  Button: ({ children, ...props }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => React.createElement("button", props, children),
  Text: ({ children, className }: React.PropsWithChildren<{ className?: string }>) => React.createElement("span", { className }, children),
  View: ({ children, className }: React.PropsWithChildren<{ className?: string }>) => React.createElement("div", { className }, children),
  Picker: ({ range, value, disabled, onChange }: { range: Array<{ name: string }>; value: number; disabled: boolean; onChange: (event: { detail: { value: string } }) => void }) =>
    React.createElement("select", { value, disabled, onChange: (event: React.ChangeEvent<HTMLSelectElement>) => onChange({ detail: { value: event.target.value } }) },
      range.map((option, index) => React.createElement("option", { key: index, value: index }, option.name))),
}));

import { GameContextReview } from "./GameContextReview";

it("shows human differences, preserves undecided mappings and sends the explicitly chosen player", async () => {
  const review: ScoresheetGameContextReview = { required: true, review_token: "secret-digest",
    differences: [{ field: "date", label: "日期", before: "2026-03-21", after: "2026-03-22" }],
    player_conflicts: [{ side: "B", row: 2, name: "球员乙", choices: [{ id: "hidden-id", name: "球员乙" }] }] };
  const div = document.createElement("div");
  document.body.append(div);
  const root = createRoot(div);
  const confirm = vi.fn().mockResolvedValue(undefined);
  try {
    await act(async () => root.render(<GameContextReview review={review} readOnly={false} busy={false} onConfirm={confirm} />));
    expect(div.textContent).toContain("原先：2026-03-21");
    expect(div.textContent).toContain("当前：2026-03-22");
    expect(div.textContent).not.toMatch(/secret-digest|hidden-id/);
    await act(async () => div.querySelector("button")!.click());
    expect(confirm).toHaveBeenLastCalledWith([]);
    const select = div.querySelector("select")!;
    await act(async () => { select.value = "1"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    await act(async () => div.querySelector("button")!.click());
    expect(confirm).toHaveBeenLastCalledWith([{ side: "B", row: 2, player_id: "hidden-id" }]);
    await act(async () => root.render(<GameContextReview review={review} readOnly={true} busy={false} onConfirm={confirm} />));
    expect(div.querySelector("button")).toBeNull();
    expect(div.querySelector("select")?.disabled).toBe(true);
  } finally {
    await act(async () => root.unmount());
    div.remove();
  }
});

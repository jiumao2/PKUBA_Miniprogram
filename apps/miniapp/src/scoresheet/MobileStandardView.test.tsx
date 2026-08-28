// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import React, { useState } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ScoresheetDocument, TeamEntry } from "@pkuba/scoresheet-domain";
import { MobileStandardView } from "./MobileStandardView";
const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "MobileStandardView.css"), "utf8");

vi.mock("@tarojs/components", () => ({
  View: ({ children, className, id, onClick }: any) => <div className={className} id={id} onClick={onClick}>{children}</div>,
  Text: ({ children, className }: any) => <span className={className}>{children}</span>,
  Button: ({ children, className, disabled, onClick }: any) => <button className={className} disabled={disabled} onClick={onClick}>{children}</button>,
  Input: ({ value, disabled, onInput }: any) => <input value={value} disabled={disabled} onInput={(event: any) => onInput?.({ detail: { value: event.target.value } })} onChange={() => {}} />,
  Picker: ({ children, disabled }: any) => <div data-disabled={disabled}>{children}</div>,
  Switch: ({ checked, disabled }: any) => <input type="checkbox" checked={checked} disabled={disabled} readOnly />,
  RootPortal: ({ children }: any) => <>{children}</>,
  ScrollView: ({ children, className, scrollY }: any) => <div data-scroll-view data-scroll-y={scrollY} className={className}>{children}</div>,
}));
vi.mock("@tarojs/taro", () => ({ default: { showModal: vi.fn() } }));
afterEach(cleanup);

function team(side: "A" | "B"): TeamEntry {
  return { side, name: `${side}队`, players: [], timeouts: [], team_fouls: [], coach_fouls: [],
    coach_post_foul_markers: [], assistant_coach_fouls: [], assistant_coach_post_foul_markers: [],
    head_coach: "", assistant_coach: "" };
}
function draft(): ScoresheetDocument {
  return { schema_version: "1.4.0", id: "drawer", revision: 1, template_id: "t", status: "draft",
    created_at: "", updated_at: "", game_prior: null, recognition: null,
    source: { original_filename: "", original_url: "", aligned_url: "", width: 1, height: 1, rotation: 0, corners: [[0, 0], [1, 0], [1, 1], [0, 1]] },
    header: { competition: "", game_number: "", date: "", scheduled_time: "", venue: "", crew_chief: "", umpire_1: "", umpire_2: "" },
    teams: [team("A"), team("B")], score_events: [], stated_period_scores: [], officials: [], acknowledged_warnings: [],
    final_score: { team_a: 0, team_b: 0, winner_name: "", ended_at: "" } };
}

describe("shared Mini scoresheet drawer", () => {
  it("keeps padding on its inner View while long player content, input and close remain usable", () => {
    function Editor() {
      const [document, setDocument] = useState(draft);
      return <MobileStandardView document={document} step="TEAM_A" readOnly={false} issues={[]} selectedScoreId="" onSelectScore={() => {}} onChange={setDocument} />;
    }
    const { container } = render(<Editor />);
    fireEvent.click(container.querySelector("#team-A-player-1")!);
    const scroll = container.querySelector<HTMLElement>(".canonical-drawer-body")!;
    expect(scroll).toHaveAttribute("data-scroll-y", "true");
    const content = scroll.querySelector<HTMLElement>(":scope > .canonical-drawer-content");
    expect(content).not.toBeNull();
    expect(within(content!).getByRole("button", { name: "清空本行" })).toBeInTheDocument();
    expect(content!.querySelectorAll("input").length).toBeGreaterThan(2);
    const longValue = "人工填写证件备注".repeat(30);
    fireEvent.input(content!.querySelector("input")!, { target: { value: longValue } });
    expect(screen.getByDisplayValue(longValue)).toBeInTheDocument();
    fireEvent.click(content!);
    expect(container.querySelector(".canonical-drawer-mask")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(container.querySelector(".canonical-drawer-mask")).toBeNull();
    expect(css.match(/\.canonical-drawer-body\s*\{([^}]+)\}/)?.[1]).not.toMatch(/padding\s*:/);
    expect(css.match(/\.canonical-drawer-content\s*\{([^}]+)\}/)?.[1]).toMatch(/padding:\s*0 20rpx 28rpx/);
  });

  it("retains the existing read-only guard without opening an editable drawer", () => {
    const onChange = vi.fn();
    const props = { document: draft(), step: "TEAM_A" as const, issues: [], selectedScoreId: "", onSelectScore: vi.fn(), onChange };
    const { container, rerender } = render(<MobileStandardView {...props} readOnly={false} />);
    fireEvent.click(container.querySelector("#team-A-player-1")!);
    expect(container.querySelector(".canonical-drawer-mask")).not.toBeNull();
    rerender(<MobileStandardView {...props} readOnly />);
    expect(container.querySelector(".canonical-drawer-mask")).toBeNull();
    fireEvent.click(container.querySelector("#team-A-player-1")!);
    expect(container.querySelector(".canonical-drawer-mask")).toBeNull();
    expect(onChange).not.toHaveBeenCalled();
  });
});

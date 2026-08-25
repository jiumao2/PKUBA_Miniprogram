import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import type { ScheduleDraft } from "@pkuba/api-client";
import { afterEach, describe, expect, it } from "vitest";

import {
  ScheduleGridEditor,
  type ScheduleGridValue,
} from "./ScheduleGridEditor";

afterEach(cleanup);

const draft: ScheduleDraft = {
  id: "draft-1",
  season_id: "season-1",
  season_version: 1,
  version: 1,
  template_version: "3.3.0",
  source_name: "",
  updated_at: "2026-08-21T00:00:00Z",
  periods: [
    { id: "period-1", code: "p1", name: "第一时段", start_time: "12:50" },
    { id: "period-2", code: "p4", name: "决赛早场", start_time: "18:30" },
  ],
  dates: [
    { date: "2026-03-21", weekday: "周六" },
    { date: "2026-03-22", weekday: "周日" },
  ],
  columns: [
    {
      id: "column-1",
      period_id: "period-1",
      period_code: "p1",
      period_name: "第一时段",
      start_time: "12:50",
      venue_name: "五四东一",
      final_only: false,
      sort_order: 1,
    },
    {
      id: "column-2",
      period_id: "period-2",
      period_code: "p4",
      period_name: "决赛早场",
      start_time: "18:30",
      venue_name: "邱德拔",
      final_only: true,
      sort_order: 2,
    },
  ],
  cells: [],
  matchup_pool: [],
  summary: {
    expected_game_count: 2,
    draft_game_count: 0,
    locked_game_count: 0,
    column_count: 2,
    calendar_day_count: 2,
  },
};

function Harness() {
  const [value, setValue] = useState<ScheduleGridValue>({
    columns: draft.columns,
    cells: draft.cells,
  });
  return (
    <>
      <ScheduleGridEditor
        draft={draft}
        value={value}
        onChange={setValue}
        onNotice={() => undefined}
      />
      <output data-testid="state">{JSON.stringify(value)}</output>
    </>
  );
}

describe("ScheduleGridEditor", () => {
  it("supports direct entry plus project-specific women and lock actions", () => {
    const { container } = render(<Harness />);
    const firstCell = container.querySelectorAll(".grid-game-cell")[0];
    fireEvent.doubleClick(firstCell);
    const editor = firstCell.querySelector("input") as HTMLInputElement;
    fireEvent.change(editor, { target: { value: "A1vsA2" } });
    fireEvent.blur(editor);

    fireEvent.click(screen.getByRole("button", { name: "设为女篮" }));
    fireEvent.click(screen.getByRole("button", { name: "领队不可调" }));

    const state = screen.getByTestId("state").textContent ?? "";
    expect(state).toContain("A1vsA2（女）");
    expect(state).toContain('"leader_adjustable":false');
    expect(firstCell.className).toContain("women");
    expect(firstCell.className).toContain("locked");
    expect(screen.getByLabelText("领队不可调")).toBeTruthy();
  });

  it("pastes a TSV block as one undoable overwrite", () => {
    const { container } = render(<Harness />);
    const grid = container.querySelector(".schedule-grid-scroll") as HTMLElement;
    fireEvent.paste(grid, {
      clipboardData: { getData: () => "A1vsA2\tA3vsA4\nA1vsA3\tA2vsA4" },
    });
    expect(screen.getByTestId("state").textContent).toContain("A2vsA4");

    fireEvent.click(screen.getByRole("button", { name: "撤回" }));
    expect(screen.getByTestId("state").textContent).not.toContain("A1vsA2");
  });

  it("mouse-drags a multi-game selection and disables batch attributes without games", () => {
    const { container } = render(<Harness />);
    const grid = container.querySelector(".schedule-grid-scroll") as HTMLElement;
    const cells = container.querySelectorAll<HTMLElement>(".grid-game-cell");
    const women = screen.getByRole("button", { name: "设为女篮" }) as HTMLButtonElement;
    const adjustable = screen.getByRole("button", { name: "允许领队调赛" }) as HTMLButtonElement;

    expect(women.disabled).toBe(true);
    expect(adjustable.disabled).toBe(true);
    fireEvent.paste(grid, {
      clipboardData: { getData: () => "A1vsA2\tA3vsA4" },
    });

    fireEvent.mouseDown(cells[2], { button: 0, buttons: 1 });
    fireEvent.mouseUp(cells[2], { button: 0, buttons: 0 });
    expect(screen.getByText("1 格区域 · 暂无比赛")).toBeTruthy();
    expect(women.disabled).toBe(true);
    expect(adjustable.disabled).toBe(true);

    fireEvent.mouseDown(cells[0], { button: 0, buttons: 1 });
    fireEvent.mouseMove(cells[1], { buttons: 1 });
    fireEvent.mouseUp(cells[1], { button: 0, buttons: 0 });
    expect(screen.getByText("2 场比赛已选 · 2 格区域")).toBeTruthy();
    expect(women.disabled).toBe(false);
    expect(adjustable.disabled).toBe(false);

    fireEvent.click(women);
    const state = screen.getByTestId("state").textContent ?? "";
    expect(state).toContain("A1vsA2（女）");
    expect(state).toContain("A3vsA4（女）");
    expect(screen.getByRole("button", { name: "拖动比赛 A1vsA2（女）" })).toBeTruthy();
    expect(cells[0].draggable).toBe(false);
  });

  it("moves an existing game only from its dedicated drag handle", () => {
    const { container } = render(<Harness />);
    const grid = container.querySelector(".schedule-grid-scroll") as HTMLElement;
    const cells = container.querySelectorAll<HTMLElement>(".grid-game-cell");
    fireEvent.paste(grid, {
      clipboardData: { getData: () => "A1vsA2" },
    });

    const transferred = new Map<string, string>();
    const dataTransfer = {
      effectAllowed: "none",
      setData: (type: string, data: string) => transferred.set(type, data),
      getData: (type: string) => transferred.get(type) ?? "",
    };
    fireEvent.dragStart(
      screen.getByRole("button", { name: "拖动比赛 A1vsA2" }),
      { dataTransfer },
    );
    fireEvent.dragOver(cells[2], { dataTransfer });
    fireEvent.drop(cells[2], { dataTransfer });

    const state = JSON.parse(screen.getByTestId("state").textContent ?? "{}") as ScheduleGridValue;
    expect(state.cells).toHaveLength(1);
    expect(state.cells[0]).toMatchObject({
      date: "2026-03-22",
      column_id: "column-1",
      matchup: "A1vsA2",
    });
  });

  it("allows adding, editing, reordering and removing dynamic columns", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "添加排期列" }));
    expect(screen.getAllByRole("combobox", { name: /列时段/ })).toHaveLength(3);

    const venue = screen.getByRole("textbox", { name: "第 3 列场地" });
    fireEvent.change(venue, { target: { value: "临时馆" } });
    expect(screen.getByTestId("state").textContent).toContain("临时馆");
    fireEvent.click(screen.getAllByRole("button", { name: "删除列" })[2]);
    expect(screen.getAllByRole("combobox", { name: /列时段/ })).toHaveLength(2);
  });

  it("zooms the complete schedule table without changing the surrounding workspace", () => {
    const { container } = render(<Harness />);
    const grid = container.querySelector(".schedule-grid-scroll") as HTMLElement;
    const table = container.querySelector(".schedule-grid-table") as HTMLElement;
    const slider = screen.getByRole("slider", { name: "表格缩放比例" }) as HTMLInputElement;
    const zoomValue = screen.getByText("100%") as HTMLOutputElement;

    expect(slider.value).toBe("100");
    expect(table.style.getPropertyValue("--schedule-grid-zoom")).toBe("1");
    fireEvent.click(screen.getByRole("button", { name: "缩小表格" }));
    expect(slider.value).toBe("90");
    expect(zoomValue.textContent).toBe("90%");
    expect(table.style.getPropertyValue("--schedule-grid-zoom")).toBe("0.9");
    fireEvent.click(screen.getByRole("button", { name: "放大表格" }));
    expect(slider.value).toBe("100");
    expect(table.style.getPropertyValue("--schedule-grid-zoom")).toBe("1");
    fireEvent.click(screen.getByRole("button", { name: "放大表格" }));
    expect(slider.value).toBe("110");
    expect(table.style.getPropertyValue("--schedule-grid-zoom")).toBe("1.1");
    fireEvent.click(screen.getByRole("button", { name: "重置表格缩放" }));

    fireEvent.change(slider, { target: { value: "50" } });
    expect((screen.getByRole("button", { name: "缩小表格" }) as HTMLButtonElement).disabled).toBe(true);
    expect(table.style.getPropertyValue("--schedule-grid-zoom")).toBe("0.5");

    expect(fireEvent.wheel(grid, {
      ctrlKey: true,
      deltaY: -100,
      clientX: 20,
      clientY: 20,
    })).toBe(false);
    expect(slider.value).toBe("60");
    fireEvent.click(screen.getByRole("button", { name: "重置表格缩放" }));
    expect(slider.value).toBe("100");
    expect(table.style.getPropertyValue("--schedule-grid-zoom")).toBe("1");
  });
});

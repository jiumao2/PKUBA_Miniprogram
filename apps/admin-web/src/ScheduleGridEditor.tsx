import {
  type CSSProperties,
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  ScheduleDraft,
  ScheduleDraftCell,
  ScheduleDraftColumn,
} from "@pkuba/api-client";

import "./schedule-grid-editor.css";

export interface ScheduleGridValue {
  columns: ScheduleDraftColumn[];
  cells: ScheduleDraftCell[];
}

type Coordinate = { row: number; column: number };
type Selection = { anchor: Coordinate; focus: Coordinate };
type ZoomFocus = { clientX?: number; clientY?: number };

const GRID_ZOOM_MIN = 50;
const GRID_ZOOM_MAX = 150;
const GRID_ZOOM_STEP = 10;
const GRID_ZOOM_DEFAULT = 100;

const cellKey = (date: string, columnId: string) => `${date}|${columnId}`;
const localId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;

const womenPattern = /\s*[（(]\s*女\s*[）)]\s*$/;

function rangeOf(selection: Selection) {
  return {
    rowStart: Math.min(selection.anchor.row, selection.focus.row),
    rowEnd: Math.max(selection.anchor.row, selection.focus.row),
    columnStart: Math.min(selection.anchor.column, selection.focus.column),
    columnEnd: Math.max(selection.anchor.column, selection.focus.column),
  };
}

function normalizeGender(matchup: string, gender: "MEN" | "WOMEN") {
  const base = matchup.replace(womenPattern, "").trim();
  return gender === "WOMEN" && base ? `${base}（女）` : base;
}

export function ScheduleGridEditor({
  draft,
  value,
  onChange,
  onNotice,
  disabled = false,
}: {
  draft: ScheduleDraft;
  value: ScheduleGridValue;
  onChange: (next: ScheduleGridValue) => void;
  onNotice: (message: string) => void;
  disabled?: boolean;
}) {
  const [selection, setSelection] = useState<Selection>({
    anchor: { row: 0, column: 0 },
    focus: { row: 0, column: 0 },
  });
  const [editing, setEditing] = useState<{ key: string; value: string } | null>(null);
  const [mouseSelecting, setMouseSelecting] = useState(false);
  const [gridZoom, setGridZoom] = useState(GRID_ZOOM_DEFAULT);
  const history = useRef<ScheduleGridValue[]>([]);
  const future = useRef<ScheduleGridValue[]>([]);
  const gridRef = useRef<HTMLDivElement>(null);
  const gridZoomRef = useRef(GRID_ZOOM_DEFAULT);
  const pendingZoomAnchor = useRef<{
    xRatio: number;
    yRatio: number;
    focusX: number;
    focusY: number;
  } | null>(null);
  const mouseSelectingRef = useRef(false);

  const updateGridZoom = useCallback((requested: number, focus: ZoomFocus = {}) => {
    const next = Math.min(
      GRID_ZOOM_MAX,
      Math.max(GRID_ZOOM_MIN, Math.round(requested / GRID_ZOOM_STEP) * GRID_ZOOM_STEP),
    );
    if (next === gridZoomRef.current) return;

    const grid = gridRef.current;
    if (grid) {
      const bounds = grid.getBoundingClientRect();
      const focusX = focus.clientX === undefined
        ? grid.clientWidth / 2
        : Math.min(grid.clientWidth, Math.max(0, focus.clientX - bounds.left));
      const focusY = focus.clientY === undefined
        ? grid.clientHeight / 2
        : Math.min(grid.clientHeight, Math.max(0, focus.clientY - bounds.top));
      pendingZoomAnchor.current = {
        xRatio: grid.scrollWidth ? (grid.scrollLeft + focusX) / grid.scrollWidth : 0,
        yRatio: grid.scrollHeight ? (grid.scrollTop + focusY) / grid.scrollHeight : 0,
        focusX,
        focusY,
      };
    }

    gridZoomRef.current = next;
    setGridZoom(next);
  }, []);

  useLayoutEffect(() => {
    const grid = gridRef.current;
    const anchor = pendingZoomAnchor.current;
    if (!grid || !anchor) return;
    grid.scrollLeft = Math.max(0, anchor.xRatio * grid.scrollWidth - anchor.focusX);
    grid.scrollTop = Math.max(0, anchor.yRatio * grid.scrollHeight - anchor.focusY);
    pendingZoomAnchor.current = null;
  }, [gridZoom]);

  useEffect(() => {
    const grid = gridRef.current;
    if (!grid) return undefined;
    const handleGridWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      if (event.deltaY === 0) return;
      updateGridZoom(
        gridZoomRef.current + (event.deltaY < 0 ? GRID_ZOOM_STEP : -GRID_ZOOM_STEP),
        { clientX: event.clientX, clientY: event.clientY },
      );
    };
    grid.addEventListener("wheel", handleGridWheel, { passive: false });
    return () => grid.removeEventListener("wheel", handleGridWheel);
  }, [updateGridZoom]);

  useEffect(() => {
    history.current = [];
    future.current = [];
    setSelection({
      anchor: { row: 0, column: 0 },
      focus: { row: 0, column: 0 },
    });
    setEditing(null);
    mouseSelectingRef.current = false;
    setMouseSelecting(false);
  }, [draft.id]);

  useEffect(() => {
    const stopMouseSelection = () => {
      mouseSelectingRef.current = false;
      setMouseSelecting(false);
    };
    window.addEventListener("mouseup", stopMouseSelection);
    window.addEventListener("blur", stopMouseSelection);
    return () => {
      window.removeEventListener("mouseup", stopMouseSelection);
      window.removeEventListener("blur", stopMouseSelection);
    };
  }, []);

  const cellsByKey = useMemo(
    () => new Map(value.cells.map((cell) => [cellKey(cell.date, cell.column_id), cell])),
    [value.cells],
  );
  const selectedRange = rangeOf(selection);

  const apply = (next: ScheduleGridValue) => {
    history.current = [...history.current.slice(-99), value];
    future.current = [];
    onChange(next);
  };

  const undo = () => {
    const previous = history.current.at(-1);
    if (!previous) return;
    history.current = history.current.slice(0, -1);
    future.current = [value, ...future.current].slice(0, 100);
    onChange(previous);
    onNotice("已撤回上一步操作");
  };

  const redo = () => {
    const next = future.current[0];
    if (!next) return;
    future.current = future.current.slice(1);
    history.current = [...history.current.slice(-99), value];
    onChange(next);
    onNotice("已重做一步操作");
  };

  const selectedCoordinates = () => {
    const result: Coordinate[] = [];
    for (let row = selectedRange.rowStart; row <= selectedRange.rowEnd; row += 1) {
      for (
        let column = selectedRange.columnStart;
        column <= selectedRange.columnEnd;
        column += 1
      ) {
        if (draft.dates[row] && value.columns[column]) result.push({ row, column });
      }
    }
    return result;
  };
  const selectedCellCoordinates = selectedCoordinates();
  const selectedGameCoordinates = selectedCellCoordinates.filter(({ row, column }) => {
    const date = draft.dates[row]?.date;
    const targetColumn = value.columns[column];
    return Boolean(
      date && targetColumn && cellsByKey.get(cellKey(date, targetColumn.id)),
    );
  });
  const hasSelectedGames = selectedGameCoordinates.length > 0;

  const replaceCells = (
    updates: Array<{
      row: number;
      column: number;
      matchup: string;
      leaderAdjustable?: boolean;
    }>,
  ) => {
    const next = new Map(value.cells.map((cell) => [cellKey(cell.date, cell.column_id), cell]));
    for (const update of updates) {
      const targetDate = draft.dates[update.row]?.date;
      const targetColumn = value.columns[update.column];
      if (!targetDate || !targetColumn) continue;
      const key = cellKey(targetDate, targetColumn.id);
      const previous = next.get(key);
      const matchup = update.matchup.trim();
      if (!matchup) {
        next.delete(key);
      } else {
        next.set(key, {
          id: previous?.id ?? localId(),
          date: targetDate,
          column_id: targetColumn.id,
          matchup,
          leader_adjustable:
            update.leaderAdjustable ?? previous?.leader_adjustable ?? true,
        });
      }
    }
    apply({ ...value, cells: [...next.values()] });
  };

  const clearSelection = () =>
    replaceCells(
      selectedCoordinates().map(({ row, column }) => ({ row, column, matchup: "" })),
    );

  const copySelection = async () => {
    const lines: string[] = [];
    for (let row = selectedRange.rowStart; row <= selectedRange.rowEnd; row += 1) {
      const fields: string[] = [];
      for (
        let column = selectedRange.columnStart;
        column <= selectedRange.columnEnd;
        column += 1
      ) {
        const targetDate = draft.dates[row]?.date;
        const targetColumn = value.columns[column];
        fields.push(
          targetDate && targetColumn
            ? (cellsByKey.get(cellKey(targetDate, targetColumn.id))?.matchup ?? "")
            : "",
        );
      }
      lines.push(fields.join("\t"));
    }
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      onNotice(`已复制 ${selectedCoordinates().length} 个单元格`);
    } catch {
      onNotice("浏览器未允许写入剪贴板，可使用系统复制快捷键重试");
    }
  };

  const pasteText = (text: string) => {
    if (!text) return;
    const rows = text.replace(/\r/g, "").split("\n");
    if (rows.at(-1) === "") rows.pop();
    const updates = rows.flatMap((line, rowOffset) =>
      line.split("\t").map((matchup, columnOffset) => ({
        row: selection.focus.row + rowOffset,
        column: selection.focus.column + columnOffset,
        matchup,
        leaderAdjustable: true,
      })),
    );
    replaceCells(updates);
    onNotice(`已粘贴并覆盖 ${updates.length} 个单元格，可一次撤回`);
  };

  const handlePaste = (event: ClipboardEvent<HTMLDivElement>) => {
    if (disabled) return;
    event.preventDefault();
    pasteText(event.clipboardData.getData("text/plain"));
  };

  const startEditing = (row: number, column: number, initial?: string) => {
    if (disabled) return;
    const targetDate = draft.dates[row]?.date;
    const targetColumn = value.columns[column];
    if (!targetDate || !targetColumn) return;
    const current = cellsByKey.get(cellKey(targetDate, targetColumn.id))?.matchup ?? "";
    setEditing({ key: cellKey(targetDate, targetColumn.id), value: initial ?? current });
  };

  const commitEditing = (row: number, column: number) => {
    if (!editing) return;
    replaceCells([{ row, column, matchup: editing.value }]);
    setEditing(null);
  };

  const moveSelection = (rowDelta: number, columnDelta: number, extend: boolean) => {
    const next = {
      row: Math.max(0, Math.min(draft.dates.length - 1, selection.focus.row + rowDelta)),
      column: Math.max(
        0,
        Math.min(value.columns.length - 1, selection.focus.column + columnDelta),
      ),
    };
    setSelection((current) => ({ anchor: extend ? current.anchor : next, focus: next }));
  };

  const beginMouseSelection = (
    event: ReactMouseEvent<HTMLTableCellElement>,
    row: number,
    column: number,
  ) => {
    if (disabled || event.button !== 0) return;
    const targetElement = event.target as HTMLElement;
    if (targetElement.closest("button, input, select, textarea, label")) return;
    event.preventDefault();
    gridRef.current?.focus({ preventScroll: true });
    mouseSelectingRef.current = true;
    setMouseSelecting(true);
    const target = { row, column };
    setSelection((current) => ({
      anchor: event.shiftKey ? current.anchor : target,
      focus: target,
    }));
  };

  const extendMouseSelection = (row: number, column: number) => {
    if (!mouseSelectingRef.current) return;
    setSelection((current) => ({ ...current, focus: { row, column } }));
  };

  const trackMouseSelection = (event: ReactMouseEvent<HTMLTableElement>) => {
    if (!mouseSelectingRef.current) return;
    const target = event.target as HTMLElement;
    const cell = target.closest<HTMLTableCellElement>(".grid-game-cell");
    if (!cell) return;
    const row = Number(cell.dataset.row);
    const column = Number(cell.dataset.column);
    if (!Number.isInteger(row) || !Number.isInteger(column)) return;
    event.preventDefault();
    extendMouseSelection(row, column);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (["INPUT", "SELECT", "BUTTON", "TEXTAREA"].includes(target.tagName)) return;
    const command = event.ctrlKey || event.metaKey;
    if (command && event.key.toLowerCase() === "z") {
      event.preventDefault();
      event.shiftKey ? redo() : undo();
      return;
    }
    if (command && event.key.toLowerCase() === "y") {
      event.preventDefault();
      redo();
      return;
    }
    if (command && event.key.toLowerCase() === "c") {
      event.preventDefault();
      void copySelection();
      return;
    }
    if (command && event.key.toLowerCase() === "v") {
      event.preventDefault();
      void navigator.clipboard
        .readText()
        .then(pasteText)
        .catch(() => onNotice("浏览器未允许读取剪贴板，请在网格内直接粘贴"));
      return;
    }
    const directions: Record<string, [number, number]> = {
      ArrowUp: [-1, 0],
      ArrowDown: [1, 0],
      ArrowLeft: [0, -1],
      ArrowRight: [0, 1],
    };
    if (event.key in directions) {
      event.preventDefault();
      const [row, column] = directions[event.key];
      moveSelection(row, column, event.shiftKey);
    } else if (event.key === "Enter") {
      event.preventDefault();
      startEditing(selection.focus.row, selection.focus.column);
    } else if (event.key === "Delete" || event.key === "Backspace") {
      event.preventDefault();
      clearSelection();
    } else if (!command && !event.altKey && event.key.length === 1) {
      event.preventDefault();
      startEditing(selection.focus.row, selection.focus.column, event.key);
    }
  };

  const setSelectedGender = (gender: "MEN" | "WOMEN") => {
    const updates = selectedGameCoordinates.flatMap(({ row, column }) => {
      const date = draft.dates[row]?.date;
      const targetColumn = value.columns[column];
      const cell = date && targetColumn
        ? cellsByKey.get(cellKey(date, targetColumn.id))
        : undefined;
      return cell
        ? [{ row, column, matchup: normalizeGender(cell.matchup, gender) }]
        : [];
    });
    if (updates.length) replaceCells(updates);
  };

  const setSelectedAdjustable = (leaderAdjustable: boolean) => {
    const updates = selectedGameCoordinates.flatMap(({ row, column }) => {
      const date = draft.dates[row]?.date;
      const targetColumn = value.columns[column];
      const cell = date && targetColumn
        ? cellsByKey.get(cellKey(date, targetColumn.id))
        : undefined;
      return cell
        ? [{
            row,
            column,
            matchup: cell.matchup,
            leaderAdjustable,
          }]
        : [];
    });
    if (updates.length) replaceCells(updates);
  };

  const updateColumn = (index: number, patch: Partial<ScheduleDraftColumn>) => {
    apply({
      ...value,
      columns: value.columns.map((column, current) =>
        current === index ? { ...column, ...patch } : column,
      ),
    });
  };

  const moveColumn = (index: number, offset: number) => {
    const target = index + offset;
    if (target < 0 || target >= value.columns.length) return;
    const columns = [...value.columns];
    [columns[index], columns[target]] = [columns[target], columns[index]];
    apply({
      ...value,
      columns: columns.map((column, current) => ({ ...column, sort_order: current + 1 })),
    });
  };

  const deleteColumn = (index: number) => {
    if (value.columns.length === 1) {
      onNotice("赛程网格至少保留一列");
      return;
    }
    const removed = value.columns[index];
    apply({
      columns: value.columns
        .filter((_, current) => current !== index)
        .map((column, current) => ({ ...column, sort_order: current + 1 })),
      cells: value.cells.filter((cell) => cell.column_id !== removed.id),
    });
    setSelection({
      anchor: { row: selection.focus.row, column: Math.max(0, index - 1) },
      focus: { row: selection.focus.row, column: Math.max(0, index - 1) },
    });
  };

  const addColumn = () => {
    if (value.columns.length >= 64) {
      onNotice("最多支持 64 个排期列");
      return;
    }
    const period = draft.periods[0];
    if (!period) return;
    apply({
      ...value,
      columns: [
        ...value.columns,
        {
          id: localId(),
          period_id: period.id,
          period_code: period.code,
          period_name: period.name,
          start_time: period.start_time,
          venue_name: "新场地",
          final_only: false,
          sort_order: value.columns.length + 1,
        },
      ],
    });
  };

  const dropOnCell = (event: DragEvent, row: number, column: number) => {
    if (disabled) return;
    event.preventDefault();
    const date = draft.dates[row]?.date;
    const targetColumn = value.columns[column];
    if (!date || !targetColumn) return;
    if (cellsByKey.get(cellKey(date, targetColumn.id))) {
      onNotice("目标单元格已有比赛，拖动不会覆盖；可先删除或使用粘贴覆盖");
      return;
    }
    const matchup = event.dataTransfer.getData("application/x-pkuba-matchup");
    if (!matchup) return;
    const source = event.dataTransfer.getData("application/x-pkuba-source");
    const updates = [{ row, column, matchup, leaderAdjustable: true }];
    if (source) {
      const parsed = JSON.parse(source) as Coordinate;
      if (parsed.row === row && parsed.column === column) return;
      updates.push({ ...parsed, matchup: "", leaderAdjustable: true });
    }
    replaceCells(updates);
  };

  return (
    <section
      className={mouseSelecting ? "schedule-grid-editor mouse-selecting" : "schedule-grid-editor"}
      aria-label="在线赛程网格编辑器"
    >
      <div className="grid-commandbar">
        <div className="grid-command-group" aria-label="撤回与剪贴板">
          <button type="button" disabled={disabled || !history.current.length} onClick={undo}>撤回</button>
          <button type="button" disabled={disabled || !future.current.length} onClick={redo}>重做</button>
          <button type="button" disabled={disabled} onClick={() => void copySelection()}>复制</button>
          <button type="button" disabled={disabled || !hasSelectedGames} onClick={clearSelection}>清空</button>
        </div>
        <span className="grid-command-separator" />
        <div className="grid-command-group" aria-label="比赛属性">
          <button type="button" title={hasSelectedGames ? "将所选比赛设为男篮" : "请先选择至少一场比赛"} disabled={disabled || !hasSelectedGames} onClick={() => setSelectedGender("MEN")}>设为男篮</button>
          <button type="button" title={hasSelectedGames ? "将所选比赛设为女篮" : "请先选择至少一场比赛"} disabled={disabled || !hasSelectedGames} onClick={() => setSelectedGender("WOMEN")}>设为女篮</button>
          <button type="button" title={hasSelectedGames ? "禁止所选比赛由领队发起调赛" : "请先选择至少一场比赛"} disabled={disabled || !hasSelectedGames} onClick={() => setSelectedAdjustable(false)}>领队不可调</button>
          <button type="button" title={hasSelectedGames ? "允许所选比赛由领队发起调赛" : "请先选择至少一场比赛"} disabled={disabled || !hasSelectedGames} onClick={() => setSelectedAdjustable(true)}>允许领队调赛</button>
        </div>
        <div className="grid-zoom-control" role="group" aria-label="表格缩放">
          <span>表格</span>
          <button
            type="button"
            aria-label="缩小表格"
            disabled={gridZoom <= GRID_ZOOM_MIN}
            onClick={() => updateGridZoom(gridZoom - GRID_ZOOM_STEP)}
          >
            −
          </button>
          <input
            type="range"
            min={GRID_ZOOM_MIN}
            max={GRID_ZOOM_MAX}
            step={GRID_ZOOM_STEP}
            value={gridZoom}
            aria-label="表格缩放比例"
            aria-valuetext={`${gridZoom}%`}
            onChange={(event) => updateGridZoom(Number(event.target.value))}
          />
          <output aria-live="polite">{gridZoom}%</output>
          <button
            type="button"
            aria-label="放大表格"
            disabled={gridZoom >= GRID_ZOOM_MAX}
            onClick={() => updateGridZoom(gridZoom + GRID_ZOOM_STEP)}
          >
            ＋
          </button>
          <button
            type="button"
            className="grid-zoom-reset"
            aria-label="重置表格缩放"
            disabled={gridZoom === GRID_ZOOM_DEFAULT}
            onClick={() => updateGridZoom(GRID_ZOOM_DEFAULT)}
          >
            重置
          </button>
        </div>
        <div className="grid-selection-status" id="grid-selection-status" aria-live="polite">
          {hasSelectedGames
            ? `${selectedGameCoordinates.length} 场比赛已选 · ${selectedCellCoordinates.length} 格区域`
            : `${selectedCellCoordinates.length} 格区域 · 暂无比赛`}
        </div>
      </div>

      <div
        className="schedule-grid-scroll"
        ref={gridRef}
        tabIndex={0}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
      >
        <table
          className="schedule-grid-table"
          style={{ "--schedule-grid-zoom": String(gridZoom / 100) } as CSSProperties}
          onMouseMove={trackMouseSelection}
        >
          <thead>
            <tr>
              <th className="grid-date-head" rowSpan={2}>日期</th>
              <th className="grid-weekday-head" rowSpan={2}>星期</th>
              {value.columns.map((column, index) => (
                <th className={column.final_only ? "grid-column-head final-only" : "grid-column-head"} key={`${column.id}-time`}>
                  <select
                    aria-label={`第 ${index + 1} 列时段`}
                    disabled={disabled}
                    value={column.period_id}
                    onChange={(event) => {
                      const period = draft.periods.find((item) => item.id === event.target.value);
                      if (period) updateColumn(index, {
                        period_id: period.id,
                        period_code: period.code,
                        period_name: period.name,
                        start_time: period.start_time,
                      });
                    }}
                  >
                    {draft.periods.map((period) => (
                      <option key={period.id} value={period.id}>{period.start_time}</option>
                    ))}
                  </select>
                </th>
              ))}
              <th className="grid-add-column-head" rowSpan={2}>
                <button type="button" disabled={disabled} onClick={addColumn} aria-label="添加排期列">＋</button>
              </th>
            </tr>
            <tr>
              {value.columns.map((column, index) => (
                <th className={column.final_only ? "grid-column-head venue final-only" : "grid-column-head venue"} key={`${column.id}-venue`}>
                  <input
                    aria-label={`第 ${index + 1} 列场地`}
                    disabled={disabled}
                    value={column.venue_name}
                    onChange={(event) => updateColumn(index, { venue_name: event.target.value })}
                  />
                  <div className="grid-column-actions">
                    <label title="该列只允许决赛">
                      <input
                        type="checkbox"
                        checked={column.final_only}
                        disabled={disabled}
                        onChange={(event) => updateColumn(index, { final_only: event.target.checked })}
                      />
                      仅决赛
                    </label>
                    <button type="button" disabled={disabled || index === 0} onClick={() => moveColumn(index, -1)} aria-label="左移列">←</button>
                    <button type="button" disabled={disabled || index === value.columns.length - 1} onClick={() => moveColumn(index, 1)} aria-label="右移列">→</button>
                    <button type="button" disabled={disabled} onClick={() => deleteColumn(index)} aria-label="删除列">×</button>
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {draft.dates.map((day, row) => (
              <tr key={day.date}>
                <th className="grid-date-cell">{day.date.slice(5)}</th>
                <th className={day.weekday === "周六" || day.weekday === "周日" ? "grid-weekday-cell weekend" : "grid-weekday-cell"}>{day.weekday}</th>
                {value.columns.map((column, columnIndex) => {
                  const key = cellKey(day.date, column.id);
                  const cell = cellsByKey.get(key);
                  const selected =
                    row >= selectedRange.rowStart && row <= selectedRange.rowEnd &&
                    columnIndex >= selectedRange.columnStart && columnIndex <= selectedRange.columnEnd;
                  const women = Boolean(cell && womenPattern.test(cell.matchup));
                  const classes = [
                    "grid-game-cell",
                    selected ? "selected" : "",
                    women ? "women" : cell ? "men" : "",
                    cell && !cell.leader_adjustable ? "locked" : "",
                    column.final_only ? "final-column" : "",
                  ].filter(Boolean).join(" ");
                  return (
                    <td
                      className={classes}
                      key={key}
                      data-row={row}
                      data-column={columnIndex}
                      aria-selected={selected}
                      onMouseDown={(event) => beginMouseSelection(event, row, columnIndex)}
                      onMouseEnter={() => extendMouseSelection(row, columnIndex)}
                      onMouseUp={() => {
                        mouseSelectingRef.current = false;
                        setMouseSelecting(false);
                      }}
                      onDoubleClick={() => startEditing(row, columnIndex)}
                      onDragOver={(event) => event.preventDefault()}
                      onDrop={(event) => dropOnCell(event, row, columnIndex)}
                      title={cell && !cell.leader_adjustable ? `${cell.matchup} · 已锁定，领队不可调` : cell?.matchup}
                    >
                      {editing?.key === key ? (
                        <input
                          autoFocus
                          value={editing.value}
                          onChange={(event) => setEditing({ key, value: event.target.value })}
                          onBlur={() => commitEditing(row, columnIndex)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") {
                              event.preventDefault();
                              commitEditing(row, columnIndex);
                            }
                            if (event.key === "Escape") setEditing(null);
                          }}
                        />
                      ) : (
                        <span className="grid-matchup-label">{cell?.matchup}</span>
                      )}
                      {cell && editing?.key !== key && (
                        <button
                          type="button"
                          className="grid-move-handle"
                          draggable={!disabled}
                          disabled={disabled}
                          aria-label={`拖动比赛 ${cell.matchup}`}
                          title="拖到空白格移动比赛"
                          onMouseDown={(event) => event.stopPropagation()}
                          onClick={(event) => event.preventDefault()}
                          onDragStart={(event) => {
                            event.dataTransfer.effectAllowed = "move";
                            event.dataTransfer.setData("application/x-pkuba-matchup", cell.matchup);
                            event.dataTransfer.setData("application/x-pkuba-source", JSON.stringify({ row, column: columnIndex }));
                          }}
                        >
                          ⠿
                        </button>
                      )}
                      {cell && !cell.leader_adjustable && <small aria-label="领队不可调">锁</small>}
                    </td>
                  );
                })}
                <td className="grid-add-column-body" />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="grid-shortcuts">
        按住鼠标左键拖过网格可框选多场比赛；格内抓手用于移动比赛且不会覆盖已有内容。双击或直接键入编辑，Shift 扩展选择，Ctrl/Cmd+C、V、Z、Y 复制、粘贴、撤回、重做；在表格内按住 Ctrl/Cmd 滚动鼠标可独立缩放网格。
      </p>
    </section>
  );
}

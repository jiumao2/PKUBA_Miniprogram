import { memo, useMemo, type KeyboardEvent, type ReactElement } from 'react';
import type {
  FoulEntry,
  PostFoulMarker,
  RegulationPeriod,
  ScoresheetDocument,
  TeamEntry,
  TeamSide,
  TemplateDefinition,
} from '../types';
import { deepCloneDocument, teamBySide } from '../types';
import { deriveScoreEvents } from '../lib/score';

type Primitive =
  | { type: 'text'; x: number; y: number; value: string; size: number; anchor?: 'start' | 'middle'; vertical?: 'baseline' | 'middle'; field: string }
  | { type: 'line'; x1: number; y1: number; x2: number; y2: number; width: number; field: string }
  | { type: 'circle'; cx: number; cy: number; radius: number; width: number; fill?: boolean; field: string };

const text = (
  x: number,
  y: number,
  value: string,
  size: number,
  field: string,
  anchor: 'start' | 'middle' = 'start',
  vertical: 'baseline' | 'middle' = 'baseline',
): Primitive => ({ type: 'text', x, y, value, size, anchor, vertical, field });

const line = (
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  width: number,
  field: string,
): Primitive => ({ type: 'line', x1, y1, x2, y2, width, field });

const circle = (
  cx: number,
  cy: number,
  radius: number,
  width: number,
  field: string,
  fill = false,
): Primitive => ({ type: 'circle', cx, cy, radius, width, field, fill });

const rowCenter = (boundaries: number[], row: number) =>
  (boundaries[row - 1] + boundaries[row]) / 2;

function foulScene(
  foul: FoulEntry,
  x1: number,
  x2: number,
  centerY: number,
  field: string,
  scale = 1,
): Primitive[] {
  const centerX = (x1 + x2) / 2;
  const baseX = centerX - (foul.free_throws ? 0.7 * scale : 0);
  const result = [text(baseX, centerY + 2.5 * scale, foul.code, 7.2 * scale, field, 'middle')];
  if ((foul.mark_style ?? 'plain') === 'circled') {
    result.push(circle(baseX, centerY, (foul.code.length === 1 ? 5 : 6) * scale, Math.max(0.45, 0.8 * scale), `${field}.circle`));
  }
  if (foul.free_throws) result.push(text(centerX + 3 * scale, centerY + 4.2 * scale, String(foul.free_throws), 4.1 * scale, field));
  if (foul.cancelled) result[0] = text(centerX, centerY + 2.5 * scale, `${foul.code}c`, (foul.code.length === 1 ? 6.9 : 6.2) * scale, field, 'middle');
  return result;
}

function postFoulScene(
  markers: PostFoulMarker[],
  x1: number,
  x2: number,
  centerY: number,
  field: string,
): Primitive[] {
  const ordered = [...(markers ?? [])].sort((a, b) => a.slot - b.slot).slice(0, 2);
  if (!ordered.length) return [];
  const width = x2 - x1;
  const centers = ordered.length === 1 ? [(x1 + x2) / 2] : [x1 + width * 0.29, x1 + width * 0.72];
  return ordered.flatMap((marker, index) => {
    const span = ordered.length === 1 ? 8 : width * 0.48;
    return foulScene(
      marker,
      centers[index] - span / 2,
      centers[index] + span / 2,
      centerY,
      `${field}.${marker.slot}`,
      ordered.length === 1 ? 0.82 : 0.62,
    );
  });
}

function unusedCoachFoulLine(
  fouls: FoulEntry[],
  bounds: [number, number][],
  centerY: number,
  field: string,
): Primitive[] {
  const lastFilledSlot = Math.max(0, ...fouls.map((foul) => foul.slot));
  if (lastFilledSlot >= bounds.length) return [];
  return [line(
    bounds[lastFilledSlot][0] + 1.2,
    centerY,
    bounds[bounds.length - 1][1] - 1.2,
    centerY,
    0.85,
    field,
  )];
}

const anchoredX = (field: { x: number; width: number; anchor?: 'start' | 'middle' }) =>
  field.anchor === 'middle' ? field.x + field.width / 2 : field.x;

function teamScene(
  team: TeamEntry,
  definition: TemplateDefinition,
): Primitive[] {
  const layout = definition.team_layouts[team.side];
  const columns = definition.player_columns;
  const side = team.side;
  const result: Primitive[] = [];
  result.push(text(anchoredX(layout.team_name), layout.team_name.baseline!, team.name, layout.team_name.font_size ?? 9.6, `team.${side}.name`, layout.team_name.anchor ?? 'start'));

  const timeouts = new Map(team.timeouts.map((entry) => [`${entry.scope}.${entry.slot}`, entry]));
  (Object.entries(layout.timeouts) as [keyof typeof layout.timeouts, { cells: [number, number, number, number][] }][]).forEach(
    ([scope, timeoutLayout]) => {
      timeoutLayout.cells.forEach(([x1, y1, x2, y2], index) => {
        const centerX = (x1 + x2) / 2;
        const centerY = (y1 + y2) / 2;
        const slot = index + 1;
        const field = `team.${side}.timeout.${scope}.${slot}`;
        const entry = timeouts.get(`${scope}.${slot}`);
        if (entry) {
          result.push(text(centerX, centerY, String(entry.minute), 7, field, 'middle', 'middle'));
        } else {
          result.push(line(x1 + 2, centerY - 1.15, x2 - 2, centerY - 1.15, 0.75, field));
          result.push(line(x1 + 2, centerY + 1.15, x2 - 2, centerY + 1.15, 0.75, field));
        }
      });
    },
  );

  const teamFouls = new Map(team.team_fouls.map((entry) => [entry.period, entry.count]));
  Object.entries(layout.team_fouls).forEach(([periodText, foulLayout]) => {
    const period = Number(periodText);
    const count = teamFouls.get(period as RegulationPeriod) ?? 0;
    foulLayout.cells.forEach(([x1, y1, x2, y2], index) => {
      const centerY = (y1 + y2) / 2;
      const field = `team.${side}.team_foul.${period}.${index + 1}`;
      if (index < count) {
        result.push(line(x1 + 2, y1 + 1.5, x2 - 2, y2 - 1.5, 1, field));
        result.push(line(x2 - 2, y1 + 1.5, x1 + 2, y2 - 1.5, 1, field));
      } else {
        result.push(line(x1 + 2, centerY - 1.15, x2 - 2, centerY - 1.15, 0.75, field));
        result.push(line(x1 + 2, centerY + 1.15, x2 - 2, centerY + 1.15, 0.75, field));
      }
    });
  });

  const players = new Map(team.players.map((player) => [player.row, player]));
  for (let row = 1; row <= 12; row += 1) {
    const player = players.get(row);
    if (!player) continue;
    const centerY = rowCenter(layout.player_rows, row);
    const baseline = centerY + 2.5;
    const prefix = `team.${side}.player.${String(row).padStart(2, '0')}`;
    result.push(text((columns.license[0] + columns.license[1]) / 2, baseline, player.license_number, 6, `${prefix}.license`, 'middle'));
    result.push(text(columns.name[0] + 3, baseline, `${player.name}${player.captain ? ' (CAP)' : ''}`, 7.1, `${prefix}.name`));
    result.push(text((columns.jersey[0] + columns.jersey[1]) / 2, baseline, player.jersey_number, 7, `${prefix}.jersey`, 'middle'));
    const participationX = (columns.participation[0] + columns.participation[1]) / 2;
    if (player.participation !== 'none') {
      result.push(line(participationX - 2.7, centerY - 2.7, participationX + 2.7, centerY + 2.7, 0.9, `${prefix}.participation`));
      result.push(line(participationX + 2.7, centerY - 2.7, participationX - 2.7, centerY + 2.7, 0.9, `${prefix}.participation`));
      if (player.participation === 'starter') result.push(circle(participationX, centerY, 5.1, 0.9, `${prefix}.participation`));
    }
    const fouls = new Map(player.fouls.map((foul) => [foul.slot, foul]));
    columns.fouls.forEach(([x1, x2], index) => {
      const slot = index + 1;
      const field = `${prefix}.foul.${slot}`;
      const foul = fouls.get(slot);
      if (foul) result.push(...foulScene(foul, x1, x2, centerY, field));
      else result.push(line(x1 + 1.2, centerY, x2 - 1.2, centerY, 0.85, field));
    });
    result.push(...postFoulScene(player.post_foul_markers ?? [], columns.post_foul[0], columns.post_foul[1], centerY, `${prefix}.post_foul`));
  }

  const lastPlayerRow = Math.max(0, ...team.players.map((player) => player.row));
  if (lastPlayerRow > 0 && lastPlayerRow < 12) {
    const closureY = rowCenter(layout.player_rows, lastPlayerRow + 1);
    result.push(line(columns.license[0], closureY, columns.participation[1], closureY, 1.1, `team.${side}.roster_closure.horizontal`));
    if (lastPlayerRow < 11) {
      result.push(line(columns.participation[1], closureY, columns.fouls[columns.fouls.length - 1][1], layout.player_rows[layout.player_rows.length - 1], 1.1, `team.${side}.roster_closure.diagonal`));
    }
  }

  const headCenter = (layout.coach_rows.head[0] + layout.coach_rows.head[1]) / 2;
  const assistantCenter = (layout.coach_rows.assistant[0] + layout.coach_rows.assistant[1]) / 2;
  result.push(text(98, headCenter + 2.4, team.head_coach, 7, `team.${side}.head_coach`));
  result.push(text(98, assistantCenter + 2.4, team.assistant_coach, 7, `team.${side}.assistant_coach`));
  team.coach_fouls.forEach((foul) => {
    const bounds = columns.coach_fouls[foul.slot - 1];
    if (!bounds) return;
    result.push(...foulScene(foul, bounds[0], bounds[1], headCenter, `team.${side}.coach_foul.${foul.slot}`));
  });
  result.push(...unusedCoachFoulLine(
    team.coach_fouls,
    columns.coach_fouls,
    headCenter,
    `team.${side}.coach_foul.unused`,
  ));
  (team.assistant_coach_fouls ?? []).forEach((foul) => {
    const bounds = columns.coach_fouls[foul.slot - 1];
    if (!bounds) return;
    result.push(...foulScene(foul, bounds[0], bounds[1], assistantCenter, `team.${side}.assistant_coach_foul.${foul.slot}`));
  });
  result.push(...unusedCoachFoulLine(
    team.assistant_coach_fouls ?? [],
    columns.coach_fouls,
    assistantCenter,
    `team.${side}.assistant_coach_foul.unused`,
  ));
  result.push(...postFoulScene(team.coach_post_foul_markers ?? [], columns.post_foul[0], columns.post_foul[1], headCenter, `team.${side}.coach_post_foul`));
  result.push(...postFoulScene(team.assistant_coach_post_foul_markers ?? [], columns.post_foul[0], columns.post_foul[1], assistantCenter, `team.${side}.assistant_coach_post_foul`));
  return result;
}

function buildScene(source: ScoresheetDocument, definition: TemplateDefinition): Primitive[] {
  const document = deriveScoreEvents(deepCloneDocument(source));
  const result: Primitive[] = [];
  const headerValues: Record<string, string> = {
    team_a_name: teamBySide(document, 'A').name,
    team_b_name: teamBySide(document, 'B').name,
    competition: document.header.competition,
    date: document.header.date,
    scheduled_time: document.header.scheduled_time,
    crew_chief: document.header.crew_chief,
    game_number: document.header.game_number,
    venue: document.header.venue,
    umpire_1: document.header.umpire_1,
    umpire_2: document.header.umpire_2,
  };
  Object.entries(headerValues).forEach(([key, value]) => {
    const field = definition.header_fields[key];
    result.push(text(anchoredX(field), field.baseline!, value, field.font_size!, `header.${key}`, field.anchor ?? 'start'));
  });
  document.teams.forEach((team) => result.push(...teamScene(team, definition)));

  const running = definition.running_score;
  document.score_events.forEach((event) => {
    const group = Math.floor((event.cumulative_score - 1) / 40);
    const row = ((event.cumulative_score - 1) % 40) + 1;
    const groupX = running.group_boundaries[group];
    const centerY = rowCenter(running.row_boundaries, row);
    const scoreX = groupX + running.cell_offsets[event.team === 'A' ? 'a_score' : 'b_score'];
    const playerX = groupX + running.cell_offsets[event.team === 'A' ? 'a_player' : 'b_player'];
    const id = `score.${event.team}.${String(event.cumulative_score).padStart(3, '0')}`;
    result.push(text(playerX, centerY + 2.4, event.scorer_jersey, 6.5, `${id}.scorer`, 'middle'));
    if (event.mark === 'filled_dot') result.push(circle(scoreX, centerY, 1.55, 0, `${id}.mark`, true));
    else if (event.mark === 'diagonal') result.push(line(scoreX - 4.6, centerY + 4.6, scoreX + 4.6, centerY - 4.6, 1.2, `${id}.mark`));
    if (event.scorer_circled) result.push(circle(playerX, centerY, 5.2, 1, `${id}.three_point`));
    const effectiveBoundary = event.boundary;
    if (effectiveBoundary !== 'none') {
      result.push(circle(scoreX, centerY, 5.3, 1.35, `${id}.boundary`));
      const start = event.team === 'A' ? groupX + 1.2 : groupX + 29.4;
      const end = event.team === 'A' ? groupX + 27 : groupX + 55.2;
      result.push(line(start, centerY + 6, end, centerY + 6, 1.3, `${id}.boundary`));
      if (effectiveBoundary === 'game_end') {
        result.push(line(start, centerY + 8.3, end, centerY + 8.3, 1.3, `${id}.boundary`));
        const nextTop = running.row_boundaries[row];
        const groupBottom = running.row_boundaries[running.row_boundaries.length - 1];
        if (nextTop < groupBottom) {
          result.push(
            line(
              start + 1,
              nextTop + 1,
              end - 1,
              groupBottom - 1,
              1.1,
              `${id}.closure`,
            ),
          );
        }
      }
    }
  });

  const summary = definition.summary_fields;
  document.stated_period_scores.forEach((score) => {
    const baseline = summary.period_baselines[score.period - 1];
    if (baseline === undefined) return;
    result.push(text(summary.period_a_x, baseline, String(score.team_a), 8, `summary.period.${score.period}.A`, 'middle'));
    result.push(text(summary.period_b_x, baseline, String(score.team_b), 8, `summary.period.${score.period}.B`, 'middle'));
  });
  result.push(text(summary.final_a.x, summary.final_a.baseline, String(document.final_score.team_a), 8.8, 'summary.final.A', 'middle'));
  result.push(text(summary.final_b.x, summary.final_b.baseline, String(document.final_score.team_b), 8.8, 'summary.final.B', 'middle'));
  result.push(text(summary.winner.x + summary.winner.width / 2, summary.winner.baseline, document.final_score.winner_name, 8, 'summary.winner', 'middle'));
  result.push(text(summary.ended_at.x + summary.ended_at.width / 2, summary.ended_at.baseline, document.final_score.ended_at, 7.8, 'summary.ended_at', 'middle'));
  document.officials.forEach((official) => {
    const layout = definition.official_fields[official.role];
    result.push(text(anchoredX(layout), layout.baseline, official.name, 7.4, `official.${official.role}.name`, layout.anchor ?? 'start'));
  });
  return result;
}

interface SceneOverlayProps {
  document: ScoresheetDocument;
  definition: TemplateDefinition;
  selectedField: string;
  onSelect: (field: string) => void;
  anomalyFields?: ReadonlySet<string>;
  opacity?: number;
}

export const SceneOverlay = memo(function SceneOverlay({
  document,
  definition,
  selectedField,
  onSelect,
  anomalyFields = new Set<string>(),
  opacity = 1,
}: SceneOverlayProps) {
  const scene = useMemo(() => buildScene(document, definition), [document, definition]);
  const hitboxClass = (field: string, base = '') => [
    base,
    selectedField === field ? 'is-selected' : '',
    anomalyFields.has(field) ? 'is-anomaly' : '',
  ].filter(Boolean).join(' ');
  const selectWithKeyboard = (event: KeyboardEvent<SVGRectElement>, field: string) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    event.stopPropagation();
    onSelect(field);
  };
  const detailRect = (
    field: string,
    parent: string,
    x: number,
    y: number,
    width: number,
    height: number,
  ) => (
    <rect
      key={`${field}-detail`}
      data-field-id={field}
      data-selection-level="detail"
      x={x}
      y={y}
      width={width}
      height={height}
      className={hitboxClass(field, 'detail-hitbox')}
      role="button"
      tabIndex={0}
      aria-label={`编辑 ${field}`}
      onClick={(event) => { event.stopPropagation(); onSelect(parent); }}
      onDoubleClick={(event) => { event.stopPropagation(); onSelect(field); }}
      onKeyDown={(event) => selectWithKeyboard(event, field)}
    />
  );
  const definedSummaryCells = definition.cells.filter((cell) => cell.id.startsWith('summary.'));
  const summaryCells = definedSummaryCells.length ? definedSummaryCells : [
    ...definition.summary_fields.period_baselines.flatMap((baseline, index) =>
      (['A', 'B'] as TeamSide[]).map((side) => ({
        id: `summary.period.${index + 1}.${side}`,
        rect: {
          x: (side === 'A' ? definition.summary_fields.period_a_x : definition.summary_fields.period_b_x) - 15,
          y: baseline - 10,
          width: 30,
          height: 13.2,
        },
      })),
    ),
    ...(['A', 'B'] as TeamSide[]).map((side) => {
      const field = side === 'A' ? definition.summary_fields.final_a : definition.summary_fields.final_b;
      return {
        id: `summary.final.${side}`,
        rect: { x: field.x - 20, y: field.baseline - 11, width: 40, height: 14 },
      };
    }),
    ...(['winner', 'ended_at'] as const).map((key) => {
      const field = definition.summary_fields[key];
      return {
        id: `summary.${key}`,
        rect: { x: field.x, y: field.baseline - 10, width: field.width, height: 13.2 },
      };
    }),
  ];
  const definedOfficialCells = definition.cells.filter((cell) => cell.id.startsWith('official.'));
  const officialCells = definedOfficialCells.length ? definedOfficialCells : Object.entries(
    definition.official_fields,
  ).map(([role, field]) => ({
    id: `official.${role}.name`,
    rect: { x: field.x, y: field.baseline - 10, width: field.width, height: 13.2 },
  }));
  const summaryBottom = definition.outer_bounds.y + definition.outer_bounds.height;

  return (
    <svg
      className="scene-overlay"
      viewBox={`0 0 ${definition.page.width} ${definition.page.height}`}
      aria-label="可编辑记录表覆盖层"
      style={{ opacity }}
    >
      <g className="scene-ink" pointerEvents="none">
        {scene.map((primitive, index) => {
          const key = `${primitive.field}-${index}`;
          if (primitive.type === 'text') {
            return (
              <text
                key={key}
                data-field-id={primitive.field}
                x={primitive.x}
                y={primitive.y}
                fontSize={primitive.size}
                textAnchor={primitive.anchor ?? 'start'}
                dominantBaseline={primitive.vertical === 'middle' ? 'central' : undefined}
                fontWeight={400}
              >
                {primitive.value}
              </text>
            );
          }
          if (primitive.type === 'line') {
            return (
              <line
                key={key}
                data-field-id={primitive.field}
                x1={primitive.x1}
                y1={primitive.y1}
                x2={primitive.x2}
                y2={primitive.y2}
                strokeWidth={primitive.width}
              />
            );
          }
          return (
            <circle
              key={key}
              data-field-id={primitive.field}
              cx={primitive.cx}
              cy={primitive.cy}
              r={primitive.radius}
              strokeWidth={primitive.width}
              className={primitive.fill ? 'filled-mark' : undefined}
            />
          );
        })}
      </g>

      <g className="scene-hitboxes">
        <rect
          data-field-id="header"
          data-selection-level="block"
          x={definition.outer_bounds.x}
          y={79}
          width={definition.outer_bounds.width}
          height={definition.team_layouts.A.section_top - 79}
          className={hitboxClass('header') || undefined}
          role="button"
          tabIndex={0}
          aria-label="编辑比赛信息"
          onClick={() => onSelect('header')}
          onKeyDown={(event) => selectWithKeyboard(event, 'header')}
        />
        {Object.entries(definition.header_fields).map(([key, rect]) =>
          detailRect(
            `header.${key}`,
            'header',
            rect.x,
            rect.y,
            rect.width,
            rect.height,
          ),
        )}
        {(['A', 'B'] as TeamSide[]).flatMap((side) => {
          const layout = definition.team_layouts[side];
          const rows: ReactElement[] = [];
          const metaField = `team.${side}.meta`;
          rows.push(
            <rect
              key={`${side}-meta`}
              data-field-id={metaField}
              data-selection-level="block"
              x={definition.outer_bounds.x}
              y={layout.section_top}
              width={definition.player_columns.fouls.at(-1)![1] - definition.outer_bounds.x}
              height={layout.player_header_top - layout.section_top}
              className={hitboxClass(metaField) || undefined}
              role="button"
              tabIndex={0}
              aria-label={`编辑 ${side} 队暂停和全队犯规`}
              onClick={() => onSelect(metaField)}
              onKeyDown={(event) => selectWithKeyboard(event, metaField)}
            />,
          );
          rows.push(detailRect(`team.${side}.name`, metaField, layout.team_name.x, layout.team_name.y, layout.team_name.width, layout.team_name.height));
          Object.entries(layout.timeouts).forEach(([scope, timeoutLayout]) => {
            timeoutLayout.cells.forEach(([x1, y1, x2, y2], index) => {
              rows.push(detailRect(`team.${side}.timeout.${scope}.${index + 1}`, metaField, x1, y1, x2 - x1, y2 - y1));
            });
          });
          Object.entries(layout.team_fouls).forEach(([period, foulLayout]) => {
            foulLayout.cells.forEach(([x1, y1, x2, y2], index) => {
              rows.push(detailRect(`team.${side}.team_foul.${period}.${index + 1}`, metaField, x1, y1, x2 - x1, y2 - y1));
            });
          });
          for (let row = 1; row <= 12; row += 1) {
            const field = `team.${side}.player.${String(row).padStart(2, '0')}`;
            const top = layout.player_rows[row - 1];
            const height = layout.player_rows[row] - top;
            rows.push(
              <rect
                key={field}
                data-field-id={field}
                data-selection-level="block"
                x={37.2}
                y={top}
                width={definition.player_columns.post_foul[1] - 37.2}
                height={height}
                className={hitboxClass(field) || undefined}
                role="button"
                tabIndex={0}
                aria-label={`编辑 ${side} 队第 ${row} 行球员`}
                onClick={() => onSelect(field)}
                onKeyDown={(event) => selectWithKeyboard(event, field)}
              />,
            );
            (['license', 'name', 'jersey', 'participation'] as const).forEach((column) => {
              const [x1, x2] = definition.player_columns[column];
              rows.push(detailRect(`${field}.${column}`, field, x1, top, x2 - x1, height));
            });
            definition.player_columns.fouls.forEach(([x1, x2], index) => {
              rows.push(detailRect(`${field}.foul.${index + 1}`, field, x1, top, x2 - x1, height));
            });
            const [postX1, postX2] = definition.player_columns.post_foul;
            rows.push(detailRect(`${field}.post_foul`, field, postX1, top, postX2 - postX1, height));
          }
          (['head', 'assistant'] as const).forEach((role) => {
            const field = `team.${side}.${role}_coach`;
            const bounds = layout.coach_rows[role];
            rows.push(
              <rect
                key={field}
                data-field-id={field}
                data-selection-level="block"
                x={definition.outer_bounds.x}
                y={bounds[0]}
                width={definition.player_columns.post_foul[1] - definition.outer_bounds.x}
                height={bounds[1] - bounds[0]}
                className={hitboxClass(field) || undefined}
                role="button"
                tabIndex={0}
                aria-label={`编辑 ${side} 队${role === 'head' ? '主教练' : '助理教练'}`}
                onClick={() => onSelect(field)}
                onKeyDown={(event) => selectWithKeyboard(event, field)}
              />,
            );
            if (role === 'head') {
              definition.player_columns.coach_fouls.forEach(([x1, x2], index) => {
                rows.push(detailRect(`team.${side}.coach_foul.${index + 1}`, field, x1, bounds[0], x2 - x1, bounds[1] - bounds[0]));
              });
              const [postX1, postX2] = definition.player_columns.post_foul;
              rows.push(detailRect(`team.${side}.coach_post_foul`, field, postX1, bounds[0], postX2 - postX1, bounds[1] - bounds[0]));
            } else {
              definition.player_columns.coach_fouls.forEach(([x1, x2], index) => {
                rows.push(detailRect(`team.${side}.assistant_coach_foul.${index + 1}`, field, x1, bounds[0], x2 - x1, bounds[1] - bounds[0]));
              });
              const [postX1, postX2] = definition.player_columns.post_foul;
              rows.push(detailRect(`team.${side}.assistant_coach_post_foul`, field, postX1, bounds[0], postX2 - postX1, bounds[1] - bounds[0]));
            }
          });
          return rows;
        })}
        {definition.running_score.group_boundaries.slice(0, 4).flatMap((groupX, group) =>
          definition.running_score.row_boundaries.slice(0, -1).flatMap((top, index) => {
            const score = group * 40 + index + 1;
            const height = definition.running_score.row_boundaries[index + 1] - top;
            return (['A', 'B'] as TeamSide[]).map((side) => {
              const x = side === 'A' ? groupX : groupX + 28.2;
              const field = `score.${side}.${String(score).padStart(3, '0')}`;
              return (
                <rect
                  key={field}
                  data-field-id={field}
                  x={x}
                  y={top}
                  width={28.2}
                  height={height}
                  className={`${selectedField.startsWith(field) ? 'is-selected' : ''}${anomalyFields.has(field) ? ' is-anomaly' : ''}`.trim() || undefined}
                  role="button"
                  tabIndex={0}
                  aria-label={`编辑 ${side} 队累积分 ${score}`}
                  onClick={() => onSelect(`${field}.edit`)}
                  onDoubleClick={(event) => { event.stopPropagation(); onSelect(`${field}.edit`); }}
                  onKeyDown={(event) => selectWithKeyboard(event, `${field}.edit`)}
                />
              );
            });
          }),
        )}
        <rect
          data-field-id="summary"
          data-selection-level="block"
          x={331.8}
          y={660.6}
          width={240.6}
          height={summaryBottom - 660.6}
          className={hitboxClass('summary') || undefined}
          role="button"
          tabIndex={0}
          aria-label="编辑得分汇总"
          onClick={() => onSelect('summary')}
          onKeyDown={(event) => selectWithKeyboard(event, 'summary')}
        />
        {summaryCells.map(({ id, rect }) =>
          detailRect(id, 'summary', rect.x, rect.y, rect.width, rect.height),
        )}
        <rect
          data-field-id="officials"
          data-selection-level="block"
          x={37.2}
          y={660.6}
          width={294.6}
          height={summaryBottom - 660.6}
          className={hitboxClass('officials') || undefined}
          role="button"
          tabIndex={0}
          aria-label="编辑记录台与裁判"
          onClick={() => onSelect('officials')}
          onKeyDown={(event) => selectWithKeyboard(event, 'officials')}
        />
        {officialCells.map(({ id, rect }) =>
          detailRect(id, 'officials', rect.x, rect.y, rect.width, rect.height),
        )}
      </g>
    </svg>
  );
});

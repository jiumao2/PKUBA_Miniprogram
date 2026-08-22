import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { makeDocument, makeTemplate } from '../test/fixtures';
import { SceneOverlay } from './SceneOverlay';

describe('interactive SVG overlay', () => {
  it('selects the stable semantic field for a clicked template cell', () => {
    const onSelect = vi.fn();
    const { container } = render(
      <SceneOverlay
        document={makeDocument()}
        definition={makeTemplate()}
        selectedField="document"
        onSelect={onSelect}
      />,
    );
    const cell = container.querySelector<SVGRectElement>('[data-field-id="team.A.player.03"]');

    expect(cell).not.toBeNull();
    fireEvent.click(cell!);
    expect(onSelect).toHaveBeenCalledWith('team.A.player.03');
  });

  it('keeps the coordinate system fixed while rendering all three score symbols', () => {
    const { container } = render(
      <SceneOverlay
        document={makeDocument()}
        definition={makeTemplate()}
        selectedField="score.A.001"
        onSelect={vi.fn()}
      />,
    );
    const svg = container.querySelector('svg')!;

    expect(svg).toHaveAttribute('viewBox', '0 0 595.32 842.04');
    expect(container.querySelector('[data-field-id="score.A.001.mark"]')).toHaveClass('filled-mark');
    expect(container.querySelector('[data-field-id="score.B.002.mark"]')?.tagName).toBe('line');
    expect(container.querySelector('[data-field-id="score.B.005.three_point"]')).toBeInTheDocument();
  });

  it('uses double click to request precise editing of a running-score cell', () => {
    const onSelect = vi.fn();
    const { container } = render(
      <SceneOverlay
        document={makeDocument()}
        definition={makeTemplate()}
        selectedField="document"
        onSelect={onSelect}
      />,
    );

    fireEvent.doubleClick(container.querySelector('rect[data-field-id="score.A.006"]')!);
    expect(onSelect).toHaveBeenLastCalledWith('score.A.006.edit');
  });

  it('uses the measured full metadata width and makes coach rows clickable', () => {
    const onSelect = vi.fn();
    const { container } = render(
      <SceneOverlay
        document={makeDocument()}
        definition={makeTemplate()}
        selectedField="team.A.meta"
        onSelect={onSelect}
      />,
    );
    const meta = container.querySelector<SVGRectElement>('[data-field-id="team.A.meta"]')!;
    const coach = container.querySelector<SVGRectElement>('rect[data-field-id="team.A.head_coach"]')!;

    expect(meta).toHaveAttribute('x', '37.2');
    expect(meta).toHaveAttribute('width', String(331.8 - 37.2));
    expect(coach).not.toBeNull();
    fireEvent.click(coach);
    expect(onSelect).toHaveBeenCalledWith('team.A.head_coach');

    const assistantFoul = container.querySelector<SVGRectElement>(
      'rect[data-field-id="team.A.assistant_coach_foul.1"]',
    )!;
    expect(assistantFoul).not.toBeNull();
    fireEvent.doubleClick(assistantFoul);
    expect(onSelect).toHaveBeenLastCalledWith('team.A.assistant_coach_foul.1');
    expect(container.querySelector('rect[data-field-id="team.A.assistant_coach_post_foul"]')).not.toBeNull();
  });

  it('closes only the unused formal coach cells with a centered horizontal line', () => {
    const document = makeDocument();
    document.teams[0].coach_fouls = [];
    document.teams[0].assistant_coach_fouls = [
      { slot: 1, code: 'C', free_throws: null, cancelled: false, period: null },
    ];
    const { container } = render(
      <SceneOverlay
        document={document}
        definition={makeTemplate()}
        selectedField="team.A.head_coach"
        onSelect={vi.fn()}
      />,
    );

    const head = container.querySelector<SVGLineElement>(
      'line[data-field-id="team.A.coach_foul.unused"]',
    )!;
    const assistant = container.querySelector<SVGLineElement>(
      'line[data-field-id="team.A.assistant_coach_foul.unused"]',
    )!;
    expect(Number(head.getAttribute('x1'))).toBeCloseTo(285.96 + 1.2, 4);
    expect(Number(head.getAttribute('x2'))).toBeCloseTo(332.28 - 1.2, 4);
    expect(Number(assistant.getAttribute('x1'))).toBeCloseTo(301.2 + 1.2, 4);
    expect(Number(assistant.getAttribute('x2'))).toBeLessThan(332.52);
  });

  it('single-clicks a block and double-clicks a precise cell with exact highlighting', () => {
    const onSelect = vi.fn();
    const { container } = render(
      <SceneOverlay
        document={makeDocument()}
        definition={makeTemplate()}
        selectedField="team.A.team_foul.2.3"
        onSelect={onSelect}
      />,
    );
    const detail = container.querySelector<SVGRectElement>(
      'rect[data-field-id="team.A.team_foul.2.3"][data-selection-level="detail"]',
    )!;

    expect(detail).toHaveClass('is-selected');
    fireEvent.click(detail);
    expect(onSelect).toHaveBeenLastCalledWith('team.A.meta');
    fireEvent.doubleClick(detail);
    expect(onSelect).toHaveBeenLastCalledWith('team.A.team_foul.2.3');
  });

  it('uses the same block and precise-cell protocol for header, summary, and officials', () => {
    const onSelect = vi.fn();
    const { container } = render(
      <SceneOverlay
        document={makeDocument()}
        definition={makeTemplate()}
        selectedField="summary.ended_at"
        onSelect={onSelect}
      />,
    );
    const targets = [
      ['header.scheduled_time', 'header'],
      ['summary.ended_at', 'summary'],
      ['summary.final.A', 'summary'],
      ['official.scorer.name', 'officials'],
    ] as const;

    targets.forEach(([field, parent]) => {
      const detail = container.querySelector<SVGRectElement>(
        `rect[data-field-id="${field}"][data-selection-level="detail"]`,
      )!;
      expect(detail).not.toBeNull();
      fireEvent.click(detail);
      expect(onSelect).toHaveBeenLastCalledWith(parent);
      fireEvent.doubleClick(detail);
      expect(onSelect).toHaveBeenLastCalledWith(field);
    });

    const summary = container.querySelector<SVGRectElement>(
      'rect[data-field-id="summary"][data-selection-level="block"]',
    )!;
    expect(Number(summary.getAttribute('height'))).toBeCloseTo(124.8, 4);
    expect(container.querySelector('rect[data-field-id="summary.ended_at"]')).toHaveClass(
      'is-selected',
    );
  });

  it('derives both game-end double lines without a manual game_end value', () => {
    const document = makeDocument();
    document.score_events.forEach((event) => { if (event.boundary === 'game_end') event.boundary = 'none'; });
    const { container } = render(
      <SceneOverlay
        document={document}
        definition={makeTemplate()}
        selectedField="score.A.006"
        onSelect={vi.fn()}
      />,
    );

    expect(container.querySelectorAll('[data-field-id="score.A.006.boundary"] line')).toHaveLength(0);
    expect(container.querySelectorAll('line[data-field-id="score.A.006.boundary"]')).toHaveLength(2);
    expect(container.querySelectorAll('line[data-field-id="score.B.005.boundary"]')).toHaveLength(2);
  });
});

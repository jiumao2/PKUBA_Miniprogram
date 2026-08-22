import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { makeDocument } from '../test/fixtures';
import type { DocumentChangeLogEntry, ScoresheetDocument, ValidationReport } from '../types';
import { Inspector } from './Inspector';

function Harness({ selectedField, validation = null, changes = [], onSelect = vi.fn(), initialDocument = makeDocument() }: {
  selectedField: string;
  validation?: ValidationReport | null;
  changes?: DocumentChangeLogEntry[];
  onSelect?: (field: string) => void;
  initialDocument?: ScoresheetDocument;
}) {
  const [document, setDocument] = useState(initialDocument);
  return (
    <>
      <Inspector
        document={document}
        selectedField={selectedField}
        validation={validation}
        changes={changes}
        onSelect={onSelect}
        onMutate={(mutation) => setDocument((current) => {
          const draft = structuredClone(current) as ScoresheetDocument;
          mutation(draft);
          return draft;
        })}
      />
      <output data-testid="document-json">{JSON.stringify(document)}</output>
    </>
  );
}

describe('semantic inspector', () => {
  it('edits a player without freehand input and records foul notation', async () => {
    const user = userEvent.setup();
    render(<Harness selectedField="team.A.player.01" />);

    const jersey = screen.getByLabelText(/球衣号码/);
    await user.clear(jersey);
    await user.type(jersey, '01');
    expect(screen.getByText('号码格式无效')).toBeInTheDocument();
    await user.clear(jersey);
    await user.type(jersey, '23');
    await user.selectOptions(screen.getByLabelText('上场状态'), 'starter');
    await user.click(screen.getByLabelText('队长（CAP）'));
    await user.selectOptions(screen.getByLabelText('犯规 1 类型'), 'T');
    await user.selectOptions(screen.getByLabelText('犯规 1 罚球下标'), '2');
    await user.selectOptions(screen.getByLabelText('犯规 1 节次'), '3');
    const cancelled = screen.getAllByRole('checkbox').find((element) => element.closest('.cancel-toggle'))!;
    expect(cancelled).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('犯规 1 罚球下标'), '');
    await user.click(cancelled);

    const document = JSON.parse(screen.getByTestId('document-json').textContent ?? '{}');
    expect(document.teams[0].players[0]).toMatchObject({
      jersey_number: '23',
      captain: true,
      participation: 'starter',
      fouls: [{ slot: 1, code: 'T', free_throws: null, cancelled: true, period: 3 }],
    });
  });

  it('locks later foul slots until the preceding slot exists and cascades clearing', async () => {
    const user = userEvent.setup();
    render(<Harness selectedField="team.A.player.01" />);

    expect(screen.getByLabelText('犯规 2 类型')).toBeDisabled();
    expect(screen.getByLabelText('犯规 5 类型')).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('犯规 1 类型'), 'P');
    expect(screen.getByLabelText('犯规 2 类型')).toBeEnabled();
    await user.selectOptions(screen.getByLabelText('犯规 2 类型'), 'T');
    expect(screen.getByLabelText('犯规 3 类型')).toBeEnabled();
    await user.selectOptions(screen.getByLabelText('犯规 1 类型'), '');

    expect(screen.getByLabelText('犯规 2 类型')).toBeDisabled();
    const document = JSON.parse(screen.getByTestId('document-json').textContent ?? '{}');
    expect(document.teams[0].players[0].fouls).toEqual([]);
  });

  it('edits exactly three coach cells and unlocks the post-foul marker afterwards', async () => {
    const user = userEvent.setup();
    render(<Harness selectedField="team.A.head_coach" />);

    expect(screen.getByText('A 队设置')).toBeInTheDocument();
    expect(screen.getByLabelText('犯规 3 类型')).toBeDisabled();
    expect(screen.queryByLabelText('犯规 4 类型')).not.toBeInTheDocument();
    expect(screen.getByLabelText('附加标记 1 类型')).toBeDisabled();
    await user.selectOptions(screen.getByLabelText('犯规 1 类型'), 'C');
    await user.selectOptions(screen.getByLabelText('犯规 2 类型'), 'B');
    await user.selectOptions(screen.getByLabelText('犯规 3 类型'), 'C');
    expect(screen.getByLabelText('附加标记 1 类型')).toBeEnabled();
    await user.selectOptions(screen.getByLabelText('附加标记 1 类型'), 'GD');

    const document = JSON.parse(screen.getByTestId('document-json').textContent ?? '{}');
    expect(document.teams[0].coach_fouls).toHaveLength(3);
    expect(document.teams[0].coach_post_foul_markers).toMatchObject([{ slot: 1, code: 'GD' }]);
  });

  it('lets the first assistant coach use the head-coach foul types after taking over', async () => {
    const user = userEvent.setup();
    render(<Harness selectedField="team.A.assistant_coach_foul.1" />);

    const firstCell = screen.getByLabelText('助理教练员犯规 1 类型');
    expect(firstCell).toHaveFocus();
    expect(screen.getByLabelText('助理教练员犯规 2 类型')).toBeDisabled();
    await user.selectOptions(firstCell, 'C');
    await user.selectOptions(screen.getByLabelText('助理教练员犯规 2 类型'), 'B');
    await user.selectOptions(screen.getByLabelText('助理教练员犯规 3 类型'), 'D');
    await user.selectOptions(screen.getByLabelText('助理教练员犯规 3 罚球下标'), '2');
    await user.selectOptions(screen.getByLabelText('助理教练员犯规 3 节次'), '3');
    expect(screen.getByLabelText('助理教练员附加标记 1 类型')).toBeEnabled();
    await user.selectOptions(screen.getByLabelText('助理教练员附加标记 1 类型'), 'GD');

    const document = JSON.parse(screen.getByTestId('document-json').textContent ?? '{}');
    expect(document.teams[0].assistant_coach_fouls).toMatchObject([
      { slot: 1, code: 'C' },
      { slot: 2, code: 'B' },
      { slot: 3, code: 'D', free_throws: 2, period: 3 },
    ]);
    expect(document.teams[0].assistant_coach_post_foul_markers).toMatchObject([{ slot: 1, code: 'GD' }]);
  });

  it('focuses the matching editor control after a precise cell is selected', () => {
    const { rerender } = render(<Harness selectedField="team.A.meta" />);
    expect(screen.getByLabelText('第 2 节')).not.toHaveFocus();

    rerender(<Harness selectedField="team.A.team_foul.2.3" />);

    expect(screen.getByLabelText('第 2 节')).toHaveFocus();
  });

  it('focuses and edits exact time, score, and official fields from a double-click selection', () => {
    const { rerender } = render(<Harness selectedField="header" />);

    rerender(<Harness selectedField="header.scheduled_time" />);
    const scheduledTime = screen.getByLabelText('计划时间');
    expect(scheduledTime).toHaveAttribute('type', 'time');
    expect(scheduledTime).toHaveFocus();
    fireEvent.change(scheduledTime, { target: { value: '16:30' } });

    rerender(<Harness selectedField="summary.ended_at" />);
    const endedAt = screen.getByLabelText('比赛结束时间');
    expect(endedAt).toHaveAttribute('type', 'time');
    expect(endedAt).toHaveFocus();
    fireEvent.change(endedAt, { target: { value: '17:45' } });

    rerender(<Harness selectedField="summary.final.A" />);
    expect(screen.getByLabelText('A 队最终比分')).toHaveFocus();

    rerender(<Harness selectedField="official.scorer.name" />);
    expect(screen.getByLabelText('记录员姓名')).toHaveFocus();

    const document = JSON.parse(screen.getByTestId('document-json').textContent ?? '{}');
    expect(document.header.scheduled_time).toBe('16:30');
    expect(document.final_score.ended_at).toBe('17:45');
  });

  it('edits recognized table personnel without assigning them to table roles', async () => {
    const user = userEvent.setup();
    const initialDocument = makeDocument();
    initialDocument.recognition = {
      run_id: 'run-table-personnel',
      notes: '',
      table_personnel: ['张三', '李四'],
      problem_paths: [],
      applied_at: '2026-08-19T00:00:00Z',
    };
    render(<Harness selectedField="officials" initialDocument={initialDocument} />);

    const first = screen.getByLabelText('记录台人员 1');
    await user.clear(first);
    await user.type(first, '王五');
    await user.click(screen.getByRole('button', { name: '删除记录台人员 2' }));
    await user.click(screen.getByRole('button', { name: '添加人员' }));

    expect(screen.getByText(/同一姓名可以用于多个岗位/)).toBeVisible();
    const document = JSON.parse(screen.getByTestId('document-json').textContent ?? '{}');
    expect(document.recognition.table_personnel).toEqual(['王五', '']);
    expect(document.officials.find((official: { role: string }) => official.role === 'scorer').name)
      .toBe('示例scorer');
  });

  it('changes a score event and recalculates all later cumulative values', async () => {
    const user = userEvent.setup();
    render(<Harness selectedField="score.A.001" />);

    await user.selectOptions(screen.getByLabelText('本次得分'), '3');
    const document = JSON.parse(screen.getByTestId('document-json').textContent ?? '{}');
    expect(document.score_events.filter((event: { team: string }) => event.team === 'A'))
      .toMatchObject([
        { points: 3, cumulative_score: 3, mark: 'diagonal', scorer_circled: true },
        { cumulative_score: 5 },
        { cumulative_score: 8 },
      ]);
  });

  it('shows unresolved recognition points but only allows choosing one two or three', async () => {
    const user = userEvent.setup();
    const initialDocument = makeDocument();
    initialDocument.score_events[0].points = null;
    initialDocument.score_events[0].mark = null;
    initialDocument.score_events[0].scorer_circled = false;
    render(<Harness selectedField="score.A.001" initialDocument={initialDocument} />);

    const points = screen.getByLabelText('本次得分') as HTMLSelectElement;
    expect([...points.options].map((option) => option.value)).toEqual(['', '1', '2', '3']);
    expect(points.options[0]).toBeDisabled();
    expect(points).toHaveValue('');

    await user.selectOptions(points, '2');
    const document = JSON.parse(screen.getByTestId('document-json').textContent ?? '{}');
    expect(document.score_events.filter((event: { team: string }) => event.team === 'A'))
      .toMatchObject([
        { points: 2, cumulative_score: 2, mark: 'diagonal', scorer_circled: false },
        { cumulative_score: 4 },
        { cumulative_score: 7 },
      ]);
  });

  it('inserts and deletes score events directly from the event ledger', async () => {
    const user = userEvent.setup();
    render(<Harness selectedField="score.A.003" />);

    expect(screen.getByRole('button', { name: '在累计 3 分之前插入' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: '在累计 3 分之前插入' }));
    let document = JSON.parse(screen.getByTestId('document-json').textContent ?? '{}');
    expect(document.score_events.filter((entry: { team: string }) => entry.team === 'A'))
      .toMatchObject([
        { cumulative_score: 1, points: 1 },
        { cumulative_score: 3, points: 2 },
        { cumulative_score: 5, points: 2 },
        { cumulative_score: 8, points: 3 },
      ]);

    await user.click(screen.getByRole('button', { name: '删除累计 3 分事件' }));
    document = JSON.parse(screen.getByTestId('document-json').textContent ?? '{}');
    expect(document.score_events.filter((entry: { team: string }) => entry.team === 'A'))
      .toMatchObject([
        { cumulative_score: 1, points: 1 },
        { cumulative_score: 3, points: 2 },
        { cumulative_score: 6, points: 3 },
      ]);
  });

  it('segments the score ledger by period and highlights a precisely selected score', async () => {
    const user = userEvent.setup();
    const { container } = render(<Harness selectedField="score.A.006.edit" />);

    expect(screen.getByRole('tab', { name: /Q2/ })).toHaveAttribute('aria-selected', 'true');
    expect(container.querySelector('[data-score-field="score.A.006"]')).toHaveClass('is-selected', 'is-targeted');
    expect(screen.queryByRole('button', { name: '删除累计 1 分事件' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '删除累计 6 分事件' })).toBeVisible();

    await user.click(screen.getByRole('tab', { name: /Q1/ }));
    expect(screen.getByRole('tab', { name: /Q1/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: '删除累计 1 分事件' })).toBeVisible();
    expect(screen.queryByRole('button', { name: '删除累计 6 分事件' })).not.toBeInTheDocument();
  });

  it('jumps from a validation issue to the related field', () => {
    const onSelect = vi.fn();
    const report: ValidationReport = {
      status: 'invalid',
      checked_at: '2026-08-18T00:00:00Z',
      issues: [{
        code: 'UNKNOWN_SCORER',
        severity: 'error',
        paths: ['/score_events/0/scorer_jersey'],
        message: '号码不在名单中',
        observed: '99',
        expected: null,
      }],
    };
    render(<Harness selectedField="document" validation={report} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole('button', { name: /UNKNOWN_SCORER/ }));
    expect(onSelect).toHaveBeenCalledWith('score.A.001.edit');
  });

  it('shows expandable human field changes without a restorable document snapshot', async () => {
    const user = userEvent.setup();
    const changes: DocumentChangeLogEntry[] = [{
      id: 7,
      document_id: 'doc-1',
      action: 'human_edit',
      summary: '人工编辑 · 1 项',
      changes: [{ path: '/teams/A/players/4/jersey_number', before: '11', after: '13' }],
      created_at: '2026-08-21T09:30:00Z',
    }];
    render(<Harness selectedField="document" changes={changes} />);

    expect(screen.getByText('人工修改记录')).toBeVisible();
    await user.click(screen.getByText('人工编辑 · 1 项'));
    expect(screen.getByText('A 队 · 第 4 行队员 · 球衣号码')).toBeVisible();
    expect(screen.getByText('11')).toBeVisible();
    expect(screen.getByText('13')).toBeVisible();
    expect(screen.queryByText(/v7/)).not.toBeInTheDocument();
  });

  it('locates and explicitly resolves one recognition uncertainty at a time', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const initialDocument = makeDocument();
    initialDocument.recognition = {
      run_id: 'run-problems', notes: '', table_personnel: [],
      problem_paths: [
        '/teams/0/assistant_coach',
        '/score_events/B/cumulative/5/scorer_jersey',
      ],
      applied_at: '2026-08-19T00:00:00Z',
    };
    render(<Harness selectedField="document" initialDocument={initialDocument} onSelect={onSelect} />);

    expect(screen.getByText('A 队助理教练员姓名未能可靠确定')).toBeVisible();
    expect(screen.getByText('B 队累计 5 分的得分号码未能可靠确定')).toBeVisible();
    await user.click(screen.getByRole('button', { name: /定位：B 队累计 5 分/ }));
    expect(onSelect).toHaveBeenCalledWith('score.B.005.edit');
    await user.click(screen.getByRole('button', { name: /已核对：A 队助理教练员/ }));

    const document = JSON.parse(screen.getByTestId('document-json').textContent ?? '{}');
    expect(document.recognition.problem_paths).toEqual([
      '/score_events/B/cumulative/5/scorer_jersey',
    ]);
  });
});

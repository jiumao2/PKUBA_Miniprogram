import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { makeDocument } from '../test/fixtures';
import type { RecognitionDiff, RecognitionRun } from '../types';
import { RecognitionPanel } from './RecognitionPanel';

const run: RecognitionRun = {
  id: 'run-1', document_id: 'doc-1', base_revision: 2, status: 'succeeded', model: 'qwen3.8-max',
  prompt_version: 'scoresheet-2026-08-19-v10',
  cached: true, auto_applied: false, applied_revision: null, recognition_notes: '一处姓名无法确认',
  usage: { input_tokens: 120, output_tokens: 80, image_tokens: 900, reasoning_tokens: 36, total_tokens: 1100 },
  error: '', result: {}, created_at: '2026-08-19T00:00:00Z', updated_at: '2026-08-19T00:00:00Z',
};
const diff: RecognitionDiff = {
  run_id: 'run-1', document_id: 'doc-1', base_revision: 2, current_revision: 3,
  regions: [
    { region: 'team_a_roster', label: 'A 队球员与犯规', changed: true, current: {}, recognized: {} },
    { region: 'summary', label: '节比分与比赛结果', changed: true, current: {}, recognized: {} },
    { region: 'officials', label: '裁判和记录台人员', changed: false, current: {}, recognized: {} },
  ],
};

describe('recognition result panel', () => {
  it('does not label manually entered personnel as a recognition result', () => {
    render(
      <RecognitionPanel
        run={null}
        diff={null}
        state="idle"
        document={makeDocument()}
        problemPaths={[]}
        tablePersonnel={['人工填写姓名']}
        onApply={vi.fn()}
        onDismissDiff={vi.fn()}
        onLocateProblem={vi.fn()}
        onResolveProblem={vi.fn()}
      />,
    );

    expect(screen.queryByLabelText('大模型识别结果')).not.toBeInTheDocument();
  });

  it('shows exact API usage and applies only selected changed regions', async () => {
    const user = userEvent.setup();
    const apply = vi.fn().mockResolvedValue(undefined);
    const locate = vi.fn();
    const resolve = vi.fn();
    render(
      <RecognitionPanel
        run={run}
        diff={diff}
        state="diff"
        document={makeDocument()}
        problemPaths={['/score_events/A/cumulative/6/scorer_jersey']}
        tablePersonnel={['张三', '李四']}
        onApply={apply}
        onDismissDiff={vi.fn()}
        onLocateProblem={locate}
        onResolveProblem={resolve}
      />,
    );
    expect(screen.getByText('总计 1100 tokens')).toBeVisible();
    expect(screen.getByText('提示词 v10')).toHaveAttribute('title', 'scoresheet-2026-08-19-v10');
    expect(screen.getByText('思考 36')).toBeVisible();
    expect(screen.getByText('一处姓名无法确认')).toBeVisible();
    expect(screen.getByLabelText('识别到的记录台人员')).toHaveTextContent('张三李四');
    expect(screen.getByText('A 队累计 6 分的得分号码未能可靠确定')).toBeVisible();
    await user.click(screen.getByRole('button', { name: /定位：A 队累计 6 分/ }));
    expect(locate).toHaveBeenCalledWith('/score_events/A/cumulative/6/scorer_jersey');
    await user.click(screen.getByRole('button', { name: /已核对：A 队累计 6 分/ }));
    expect(resolve).toHaveBeenCalledWith('/score_events/A/cumulative/6/scorer_jersey');
    await user.click(screen.getByText('节比分与比赛结果'));
    await user.click(screen.getByRole('button', { name: /应用所选 1 个区域/ }));
    expect(apply).toHaveBeenCalledWith(['team_a_roster']);
  });

  it('shows and resolves a typed score warning independently', async () => {
    const user = userEvent.setup();
    const resolve = vi.fn();
    render(
      <RecognitionPanel
        run={run}
        diff={null}
        state="applied"
        document={makeDocument()}
        problemPaths={[]}
        issues={[{
          code: 'RUNNING_SCORE_POINTS_MISMATCH',
          path: '/score_events/B/cumulative/5/points',
          message: 'B 队累计 5 分的模型分值与相邻号码行不一致。',
          observed: 2,
          expected: 1,
        }]}
        onApply={vi.fn()}
        onDismissDiff={vi.fn()}
        onLocateProblem={vi.fn()}
        onResolveProblem={resolve}
      />,
    );

    expect(screen.getByText('B 队累计 5 分的模型分值与相邻号码行不一致。')).toBeVisible();
    await user.click(screen.getByRole('button', { name: /已核对：B 队累计 5 分/ }));
    expect(resolve).toHaveBeenCalledWith(
      '/score_events/B/cumulative/5/points',
      'RUNNING_SCORE_POINTS_MISMATCH',
    );
  });
});

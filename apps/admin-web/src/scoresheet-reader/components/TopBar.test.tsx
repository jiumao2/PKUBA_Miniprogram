import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { makeDocument } from '../test/fixtures';
import { TopBar } from './TopBar';

const handlers = {
  onChooseGame: vi.fn(), onRecognize: vi.fn(), onUndo: vi.fn(), onRedo: vi.fn(),
  onSave: vi.fn(), onValidate: vi.fn(), onConfirm: vi.fn(),
  onToggleSource: vi.fn(), onToggleInspector: vi.fn(),
  onReturn: vi.fn(),
};

describe('formal top bar', () => {
  it('shows an empty product state with an explicit return to competition media', async () => {
    const user = userEvent.setup();
    render(
      <TopBar
        document={null}
        validation={null}
        saveState="idle"
        canUndo={false}
        canRedo={false}
        recognitionMode="mock"
        recognitionState="idle"
        sourceOpen
        inspectorOpen
        {...handlers}
      />,
    );

    expect(screen.getByTestId('scoresheet-logo')).toBeVisible();
    expect(screen.getByRole('button', { name: /返回比赛资料/ })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: /返回比赛资料/ }));
    expect(handlers.onReturn).toHaveBeenCalledOnce();
    expect(screen.getAllByText('尚未选择比赛').length).toBeGreaterThan(0);
    expect(screen.queryByText('合成样表')).not.toBeInTheDocument();
    expect(screen.queryByText(/^v\d+/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /保存草稿/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^校验/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /提交记录表/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /选择比赛/ })).toBeEnabled();
  });

  it('identifies a real record by matchup, competition and current status', () => {
    const document = makeDocument('real-document');
    document.game_prior = {
      game_id: 'game-1', competition: '2026 北大杯', division: '男篮', date: '2026-08-21',
      scheduled_time: '19:00', venue: '体育馆', source_hash: 'hash', locked_paths: [],
      team_a: { team_id: 'a', name: '数学', player_names: [] },
      team_b: { team_id: 'b', name: '外院', player_names: [] },
    };
    document.status = 'needs_review';

    render(
      <TopBar
        document={document}
        validation={null}
        saveState="saved"
        canUndo={false}
        canRedo={false}
        recognitionMode="mock"
        recognitionState="applied"
        sourceOpen
        inspectorOpen
        {...handlers}
      />,
    );

    expect(screen.getByText('数学 vs 外院')).toBeVisible();
    expect(screen.getByText('2026 北大杯 · 待人工核对')).toBeVisible();
    expect(screen.queryByText(/^v\d+/)).not.toBeInTheDocument();
  });
});

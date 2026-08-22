import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { GameSummary } from '../types';
import { GameBrowser } from './GameBrowser';

const games: GameSummary[] = [
  {
    id: 'ready', competition: '公开测试赛', division: '男甲', date: '2026-08-19',
    scheduled_time: '14:00', venue: '第一体育馆', team_a_name: '数学', team_b_name: '外院',
    ready: true, unavailable_reason: '', document_id: null, scoresheet_state: 'not_uploaded',
  },
  {
    id: 'recognized', competition: '公开测试赛', division: '男甲', date: '2026-08-19',
    scheduled_time: '16:00', venue: '第一体育馆', team_a_name: '物院', team_b_name: '化院',
    ready: true, unavailable_reason: '', document_id: 'recognized-document',
    scoresheet_state: 'recognized',
  },
  {
    id: 'pending', competition: '公开测试赛', division: '决赛', date: '2026-08-20',
    scheduled_time: '18:00', venue: '第一体育馆', team_a_name: '半决赛胜者', team_b_name: '待定',
    ready: false, unavailable_reason: '球队尚未确定', document_id: null,
    scoresheet_state: 'not_uploaded',
  },
];

describe('game browser', () => {
  it('filters games and prevents uploads for unresolved placeholders', async () => {
    const user = userEvent.setup();
    render(<GameBrowser games={games} loading={false} onClose={vi.fn()} onRefresh={vi.fn()} onOpen={vi.fn()} onUpload={vi.fn()} onReupload={vi.fn()} />);
    expect(screen.getByRole('button', { name: /半决赛胜者/ })).toBeDisabled();
    await user.type(screen.getByPlaceholderText(/搜索球队/), '数学');
    expect(screen.getByRole('button', { name: /数学.*外院/ })).toBeVisible();
    expect(screen.queryByRole('button', { name: /半决赛胜者/ })).not.toBeInTheDocument();
  });

  it('opens an existing recognized document directly from its game row', async () => {
    const user = userEvent.setup();
    const open = vi.fn().mockResolvedValue(undefined);
    const close = vi.fn();
    render(
      <GameBrowser
        games={games}
        loading={false}
        onClose={close}
        onRefresh={vi.fn()}
        onOpen={open}
        onUpload={vi.fn()}
        onReupload={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: /物院.*化院.*已识别/ })).toBeVisible();
    await user.click(screen.getByRole('button', { name: /物院.*化院.*已识别/ }));

    expect(open).toHaveBeenCalledWith('recognized-document');
    expect(close).toHaveBeenCalledOnce();
  });

  it('confirms replacement and sends the selected file to reupload', async () => {
    const user = userEvent.setup();
    const reupload = vi.fn().mockResolvedValue(undefined);
    const close = vi.fn();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { container } = render(
      <GameBrowser
        games={games}
        loading={false}
        onClose={close}
        onRefresh={vi.fn()}
        onOpen={vi.fn()}
        onUpload={vi.fn()}
        onReupload={reupload}
      />,
    );

    await user.click(screen.getByRole('button', { name: '重新上传' }));
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['same image'], 'same.png', { type: 'image/png' });
    await user.upload(input, file);

    expect(reupload).toHaveBeenCalledWith('recognized-document', file);
    expect(close).toHaveBeenCalledOnce();
  });
});

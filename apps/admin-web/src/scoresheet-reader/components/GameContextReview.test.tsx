import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ScoresheetGameContextReview } from '@pkuba/scoresheet-domain';
import { GameContextReview } from './GameContextReview';

const review: ScoresheetGameContextReview = {
  required: true, review_token: 'internal-signed-digest-must-not-be-shown',
  differences: [{ field: 'venue', label: '场地', before: '五四东一', after: '邱德拔' }],
  player_conflicts: [{ side: 'A', row: 1, name: '球员甲', choices: [{ id: 'private-player-id', name: '球员甲' }] }],
};

describe('match context review', () => {
  it('shows precise differences and submits only after explicit consent and player choice', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const { container } = render(<GameContextReview review={review} readOnly={false} busy={false} onConfirm={onConfirm} />);
    expect(screen.getByText('原先：五四东一')).toBeInTheDocument();
    expect(screen.getByText('当前：邱德拔')).toBeInTheDocument();
    expect(container.textContent).not.toContain(review.review_token);
    expect(container.textContent).not.toContain('private-player-id');
    const submit = screen.getByRole('button', { name: '保留编辑并确认复核' });
    expect(submit).toBeDisabled();
    await user.selectOptions(screen.getByRole('combobox'), 'private-player-id');
    await user.click(screen.getByRole('checkbox'));
    await user.click(submit);
    expect(onConfirm).toHaveBeenCalledExactlyOnceWith([{ side: 'A', row: 1, player_id: 'private-player-id' }]);
  });

  it('does not silently choose a player or expose a write action to readonly users', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<GameContextReview review={review} readOnly={true} busy={false} onConfirm={onConfirm} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.getByRole('combobox')).toBeDisabled();
    rerender(<GameContextReview review={review} readOnly={false} busy={false} onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole('checkbox'));
    await userEvent.click(screen.getByRole('button'));
    expect(onConfirm).toHaveBeenCalledExactlyOnceWith([]);
  });
});

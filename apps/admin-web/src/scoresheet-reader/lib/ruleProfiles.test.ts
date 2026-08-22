import { describe, expect, it } from 'vitest';
import { foulEditorOptions } from './ruleProfiles';

describe('rule profile foul options', () => {
  it('derives every active editor group from the shared rule profile', () => {
    const codes = (group: 'player' | 'coach' | 'post_foul') => (
      foulEditorOptions('fiba_2024', group).map((option) => option.code)
    );

    expect(codes('player')).toEqual(['P', 'T', 'U', 'D']);
    expect(codes('coach')).toEqual(['C', 'B', 'D', 'F']);
    expect(codes('post_foul')).toEqual(['D', 'GD', 'F']);
  });

  it('allows every approved suffix on a coach disqualifying foul', () => {
    const option = foulEditorOptions('fiba_2024', 'coach')
      .find((entry) => entry.code === 'D');

    expect(option?.allowedSuffixes).toEqual(['', '1', '2', '3', 'c']);
  });
});

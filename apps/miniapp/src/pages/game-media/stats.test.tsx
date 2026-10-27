// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const fixture = vi.hoisted(() => ({ stats: null as any }));
vi.mock('@tarojs/components', () => ({
  View: ({ children, className }: any) => <div className={className}>{children}</div>,
  Text: ({ children, className }: any) => <span className={className}>{children}</span>,
  Image: () => <span />,
  Button: ({ children }: any) => <button>{children}</button>,
}));
vi.mock('@tarojs/taro', async () => {
  const react = await import('react');
  return {
    default: {},
    useRouter: () => ({ params: { id: 'synthetic-game' } }),
    useDidShow: (callback: () => void) => react.useEffect(callback, []),
  };
});
vi.mock('../../sharing', () => ({ gameShareOptions: vi.fn(), usePublicPageShare: vi.fn() }));
vi.mock('../../auth', () => ({
  getMiniAppSession: () => null,
  resolveMiniAppIdentity: async () => ({ token: null, me: null }),
}));
vi.mock('../../api', () => ({
  absoluteMediaUrl: (url: string) => url,
  replaceGameMedia: vi.fn(), uploadGameMedia: vi.fn(),
  api: { getGameDetail: async () => ({
    game: { id: 'synthetic-game', date: '2026-10-26', start_time: '18:00',
      division_name: '合成组', home_name: '甲队', away_name: '乙队',
      home_score: 2, away_score: 0, venue_name: '合成场地' },
    group_photos: [], stats: fixture.stats,
  }) },
}));
import Page from './index';

let root: Root;
let container: HTMLDivElement;
const player = (name: string, teamId: string, appeared = false, starter = false) => ({
  player_id: name, player_name: name, team_id: teamId, team_name: teamId === 'a' ? '甲队' : '乙队',
  jersey_number: '8', appeared, starter, points: 0, one_point_events: 0,
  two_point_events: 0, three_point_events: 0, personal_fouls: 0, foul_types: [],
});
async function mount() {
  await act(async () => { root.render(<Page />); });
}
beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  fixture.stats = { home_name: '甲队', away_name: '乙队', home_score: 2, away_score: 0,
    team_stats: [{ side: 'A', team_id: 'a' }, { side: 'B', team_id: 'b' }],
    player_stats: [player('首发队员', 'a', true, true), player('替补队员', 'a', true),
      player('未出场甲', 'a'), player('未出场乙', 'b')],
  };
  container = document.createElement('div');
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

describe('published game registered players', () => {
  it('shows all registered players, including nonparticipants with zero statistics', async () => {
    await mount();
    expect(container.querySelectorAll('.player-detail-row')).toHaveLength(4);
    expect(container.querySelector('.side-a')?.textContent).toContain('未出场甲');
    expect(container.querySelector('.side-a')?.textContent).not.toContain('未出场乙');
    expect(container.querySelector('.side-b')?.textContent).toContain('未出场乙');
    expect(container.querySelectorAll('.starter-badge')).toHaveLength(1);
    expect(container.querySelector('.side-b .player-name')?.textContent).toBe('#8 未出场乙');
    expect(container.querySelector('.side-b .player-detail-row')?.textContent).toContain('00/0/00');
    expect(container.textContent).not.toContain('未上场');
  });
  it('keeps an empty team separate from the other registered players', async () => {
    fixture.stats.player_stats = [player('未出场甲', 'a')];
    await mount();
    expect(container.querySelector('.side-a')?.textContent).toContain('未出场甲');
    expect(container.querySelector('.side-b')?.textContent).toContain('本队暂无球员数据');
  });
  it('keeps the empty-publication state', async () => {
    fixture.stats.player_stats = [];
    await mount();
    expect(container.textContent).toContain('本场暂无球员数据');
  });
  it('does not expose unpublished players when there is no current publication', async () => {
    fixture.stats = null;
    await mount();
    expect(container.querySelectorAll('.player-detail-row')).toHaveLength(0);
    expect(container.textContent).toContain('本场暂无已发布的球员数据');
  });
});

// @vitest-environment jsdom
import React from 'react';
import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({
  shown: null as null | (() => void),
  api: {
    getCurrentSeason: vi.fn(), getTeamLeaderboard: vi.fn(),
    getPlayerLeaderboard: vi.fn(), getPublishedGameSummaries: vi.fn(),
  },
}));
vi.mock('@tarojs/taro', () => ({ useDidShow: (callback: () => void) => { state.shown = callback; } }));
vi.mock('@tarojs/components', () => ({
  View: ({ children, className }: any) => <div className={className}>{children}</div>,
  Text: ({ children, className }: any) => <span className={className}>{children}</span>,
  Button: ({ children, className, onClick, disabled }: any) => <button className={className} onClick={onClick} disabled={disabled}>{children}</button>,
  ScrollView: ({ children, className }: any) => <div className={className}>{children}</div>,
}));
vi.mock('../../api', () => ({ api: state.api }));
vi.mock('../../navigation', () => ({ navigateToOnce: vi.fn() }));
vi.mock('../../tabbar', () => ({ syncTabBar: vi.fn() }));
import DataPage from './index';

afterEach(cleanup);

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const division = (id: string, name: string) => ({ id, code: id === 'a' ? 'men-a' : 'women-a', name, gender: 'MEN' });
const season = (...divisions: ReturnType<typeof division>[]) => ({ id: divisions[0]?.id, divisions });
function leaderboard(id: string, name: string) {
  return { items: [{ team_id: id, team_name: name, division_id: id, division_name: id === 'a' ? '甲季组别' : '乙季组别', division_gender: 'MEN', rank: 1, wins: 1, losses: 0, points_for: 10, points_against: 2, points_per_game: 10, points_against_per_game: 2, point_difference_per_game: 8, win_percentage: 100, games_played: 1 }], total: 1, page: 1, page_size: 100 };
}
beforeEach(() => {
  vi.clearAllMocks();
  state.shown = null;
  state.api.getPlayerLeaderboard.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
  state.api.getPublishedGameSummaries.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 100 });
});

describe('DEF042 fixed-source DataPage request-context probes (React + mocked API, not WeChat)', () => {
  it('manual division B failure does not retain A rows (control)', async () => {
    const next = deferred<ReturnType<typeof leaderboard>>();
    state.api.getCurrentSeason.mockResolvedValue(season(division('a', '甲组'), division('b', '乙组')));
    state.api.getTeamLeaderboard.mockImplementation((query: string) => query.includes('division_id=a') ? Promise.resolve(leaderboard('a', '甲季球队')) : next.promise);
    render(<DataPage />);
    await screen.findByText('甲季球队');
    fireEvent.click(screen.getByRole('button', { name: '乙组' }));
    await waitFor(() => expect(state.api.getTeamLeaderboard).toHaveBeenCalledWith(expect.stringContaining('division_id=b')));
    expect(screen.queryByText('甲季球队')).not.toBeInTheDocument();
    await act(async () => { next.reject(new Error('乙组读取失败')); });
    expect(await screen.findByText('乙组读取失败')).toBeInTheDocument();
    expect(screen.queryByText('甲季球队')).not.toBeInTheDocument();
  });
  it('a late A result cannot overwrite manually selected B (control)', async () => {
    const old = deferred<ReturnType<typeof leaderboard>>();
    state.api.getCurrentSeason.mockResolvedValue(season(division('a', '甲组'), division('b', '乙组')));
    state.api.getTeamLeaderboard.mockImplementation((query: string) => query.includes('division_id=a') ? old.promise : Promise.resolve(leaderboard('b', '乙季球队')));
    render(<DataPage />);
    await screen.findByRole('button', { name: '乙组' });
    fireEvent.click(screen.getByRole('button', { name: '乙组' }));
    await screen.findByText('乙季球队');
    await act(async () => { old.resolve(leaderboard('a', '迟到甲球队')); });
    expect(screen.getByText('乙季球队')).toBeInTheDocument();
    expect(screen.queryByText('迟到甲球队')).not.toBeInTheDocument();
  });
  it('foreground refresh in the same season retains the selected division and refetches rows (control)', async () => {
    let fresh = false;
    state.api.getCurrentSeason.mockResolvedValue(season(division('a', '甲组'), division('b', '乙组')));
    state.api.getTeamLeaderboard.mockImplementation((query: string) => Promise.resolve(query.includes('division_id=a') ? leaderboard('a', '甲季球队') : leaderboard('b', fresh ? '乙组新数据' : '乙组原数据')));
    render(<DataPage />);
    await screen.findByText('甲季球队');
    fireEvent.click(screen.getByRole('button', { name: '乙组' }));
    await screen.findByText('乙组原数据');
    fresh = true;
    await act(async () => { state.shown?.(); });
    await screen.findByText('乙组新数据');
    expect(screen.getByRole('button', { name: '乙组' }).className).toContain('active');
    expect(state.api.getCurrentSeason).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('乙组原数据')).not.toBeInTheDocument();
  });
  it.each(['pending', 'failed'] as const)('foreground current-season A to B must hide A rows while B is %s', async (terminal) => {
    const nextSeason = deferred<ReturnType<typeof season>>();
    const nextRows = deferred<ReturnType<typeof leaderboard>>();
    state.api.getCurrentSeason.mockResolvedValueOnce(season(division('a', '甲季组别'))).mockImplementation(() => nextSeason.promise);
    state.api.getTeamLeaderboard.mockImplementation((query: string) => query.includes('division_id=a') ? Promise.resolve(leaderboard('a', '甲季球队')) : nextRows.promise);
    render(<DataPage />);
    await screen.findByText('甲季球队');
    await act(async () => { state.shown?.(); });
    await act(async () => { nextSeason.resolve(season(division('b', '乙季组别'))); });
    await waitFor(() => expect(state.api.getTeamLeaderboard).toHaveBeenCalledWith(expect.stringContaining('division_id=b')));
    expect(screen.getByRole('button', { name: '乙季组别' }).className).toContain('active');
    if (terminal === 'failed') {
      await act(async () => { nextRows.reject(new Error('乙季榜单失败')); });
      expect(await screen.findByText('乙季榜单失败')).toBeInTheDocument();
    } else {
      expect(screen.getByText(/正在更新|正在读取数据/)).toBeInTheDocument();
    }
    expect(screen.queryByText('甲季球队')).not.toBeInTheDocument();
  });
});

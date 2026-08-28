import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const state = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
vi.mock('./store', () => ({ useEditorStore: () => state.current }));
vi.mock('./components/DocumentCanvas', () => ({ DocumentCanvas: () => null }));
vi.mock('./components/Inspector', () => ({ Inspector: () => null }));
beforeEach(() => {
  state.current = { loading: false, template: null, document: null, games: [], error: '', dirty: false,
    initialize: vi.fn(), syncNow: vi.fn(), heartbeatLease: vi.fn(), releaseLease: vi.fn(),
    setOnline: vi.fn(), undo: vi.fn(), redo: vi.fn(), save: vi.fn() };
});
afterEach(cleanup);

describe('environment-neutral scoresheet startup', () => {
  it('shows the workbench title without a local-environment label while loading', () => {
    state.current.loading = true;
    const { container } = render(<App />);
    expect(screen.getByText('正在打开记录表工作台…')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/本地|FastAPI/);
  });
  it('preserves the actual service error', () => {
    state.current.error = '暂时无法读取模板，请稍后重试。';
    render(<App />);
    expect(screen.getByText('暂时无法读取模板，请稍后重试。')).toBeInTheDocument();
  });
  it('offers a neutral retry when no detailed error is available', () => {
    const { container } = render(<App />);
    expect(screen.getByText('模板定义未加载，请刷新页面重试。')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/本地|FastAPI/);
  });
});

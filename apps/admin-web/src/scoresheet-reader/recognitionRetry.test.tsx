import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import { TopBar } from './components/TopBar';
import { useEditorStore } from './store';
import { makeDocument } from './test/fixtures';
import type { RecognitionRun } from './types';

const failed: RecognitionRun = {
  id: 'failed-run', document_id: 'retry-sheet', base_revision: 2, status: 'failed',
  model: 'fixture', prompt_version: 'fixture', can_retry: true, cached: false,
  auto_applied: false, applied_revision: null, recognition_notes: '', error: '识别失败',
  usage: { input_tokens: 0, output_tokens: 0, image_tokens: 0, reasoning_tokens: 0, total_tokens: 0 },
  result: null, created_at: '2026-08-28T00:00:00Z', updated_at: '2026-08-28T00:00:00Z',
};

function Harness() {
  const state = useEditorStore();
  return <TopBar document={state.document} validation={null} saveState={state.saveState}
    canUndo={false} canRedo={false} recognitionMode="Qwen" recognitionState={state.recognitionState}
    recognitionRetryAllowed={state.recognitionRun?.can_retry === true}
    onChooseGame={() => undefined} onRecognize={state.recognize} onUndo={() => undefined}
    onRedo={() => undefined} onSave={state.save} onValidate={state.validate} onConfirm={state.confirm}
    sourceOpen inspectorOpen onToggleSource={() => undefined} onToggleInspector={() => undefined}
    onReturn={() => undefined} readOnly={state.readOnly} />;
}

beforeEach(() => {
  vi.restoreAllMocks();
  const document = makeDocument('retry-sheet');
  document.revision = 2;
  document.header.crew_chief = '人工主裁';
  document.source.original_url = '/fixture-source.png';
  document.game_prior = {
    game_id: 'fixture-game', competition: '北大杯', division: '男甲', date: '2026-08-28',
    scheduled_time: '12:50', venue: '五四东一', source_hash: 'fixture', locked_paths: [],
    team_a: { team_id: 'a', name: '数学', player_names: [] },
    team_b: { team_id: 'b', name: '外院', player_names: [] },
  };
  useEditorStore.setState({ ...useEditorStore.getInitialState(), document, serverRevision: 2,
    readOnly: false, online: true, loading: false, saveState: 'saved',
    recognitionRun: { ...failed }, recognitionState: 'failed' });
  vi.spyOn(api, 'games').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, division_names: [] });
  vi.spyOn(api, 'changes').mockResolvedValue({ items: [], next_before_id: null });
  vi.spyOn(api, 'leaseState').mockReturnValue({ token: 'fixture', readOnly: false, reason: '', holder: null, event: 0 });
  vi.spyOn(api, 'createRecognition').mockRejectedValue(new Error('模拟服务失败'));
  vi.spyOn(window, 'confirm').mockReturnValue(false);
});

describe('failed recognition replacement consent', () => {
  it('describes the full overwrite and cancelling sends no request or save', async () => {
    const save = vi.spyOn(api, 'save');
    render(<Harness />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /重试识别/ })); });
    expect(window.confirm).toHaveBeenCalledWith(expect.stringMatching(/覆盖整张草稿.*人工修改.*失败.*保留/));
    expect(api.createRecognition).not.toHaveBeenCalled();
    expect(save).not.toHaveBeenCalled();
    expect(useEditorStore.getState().document?.header.crew_chief).toBe('人工主裁');
  });

  it('an accepted retry sends explicit confirmation and failure preserves manual content', async () => {
    vi.mocked(window.confirm).mockReturnValue(true);
    const before = structuredClone(useEditorStore.getState().document);
    render(<Harness />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /重试识别/ })); });
    expect(api.createRecognition).toHaveBeenCalledExactlyOnceWith('retry-sheet', 2, true);
    expect(useEditorStore.getState().document).toEqual(before);
    expect(useEditorStore.getState().error).toBe('模拟服务失败');
  });

  it('claims the retry before an outstanding manual save to prevent duplicate submissions', async () => {
    vi.mocked(window.confirm).mockReturnValue(true);
    let finishSave!: () => void;
    const pendingSave = new Promise<void>((resolve) => { finishSave = resolve; });
    useEditorStore.setState({ dirty: true, save: async () => {
      await pendingSave;
      useEditorStore.setState({ dirty: false, saveState: 'saved', serverRevision: 3 });
    } });
    const first = useEditorStore.getState().recognize();
    await useEditorStore.getState().recognize();
    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(api.createRecognition).not.toHaveBeenCalled();
    finishSave();
    await first;
    expect(api.createRecognition).toHaveBeenCalledExactlyOnceWith('retry-sheet', 3, true);
  });

  it('does not queue after the pending save reveals a published record', async () => {
    vi.mocked(window.confirm).mockReturnValue(true);
    useEditorStore.setState({ dirty: true, save: async () => {
      const document = structuredClone(useEditorStore.getState().document!);
      document.status = 'confirmed';
      useEditorStore.setState({ document, dirty: false, saveState: 'saved' });
    } });
    await useEditorStore.getState().recognize();
    expect(api.createRecognition).not.toHaveBeenCalled();
    expect(useEditorStore.getState().document?.status).toBe('confirmed');
  });

  it('replaces the displayed manual draft when the successful run was applied by the server', async () => {
    vi.mocked(window.confirm).mockReturnValue(true);
    const recognized = structuredClone(useEditorStore.getState().document!);
    recognized.revision = 3;
    recognized.header.crew_chief = '新识别主裁';
    vi.mocked(api.createRecognition).mockResolvedValue({ ...failed, status: 'succeeded',
      can_retry: false, auto_applied: true, applied_revision: 3 });
    vi.spyOn(api, 'document').mockResolvedValue(recognized);
    render(<Harness />);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /重试识别/ })); });
    expect(useEditorStore.getState().document?.header.crew_chief).toBe('新识别主裁');
    expect(useEditorStore.getState().serverRevision).toBe(3);
    expect(useEditorStore.getState().validation).toBeNull();
    expect(screen.queryByRole('button', { name: /重试识别/ })).not.toBeInTheDocument();
  });

  it.each(['confirmed', 'superadmin-correction'] as const)(
    'never retries a published record (%s)', async (kind) => {
      const document = structuredClone(useEditorStore.getState().document!);
      if (kind === 'confirmed') document.status = 'confirmed';
      useEditorStore.setState({ document, recognitionRun: { ...failed,
        can_retry: kind !== 'superadmin-correction' } });
      render(<Harness />);
      expect(screen.getByRole('button', { name: /重试识别/ })).toBeDisabled();
      // A stale/programmatic callback must not bypass the rendered disabled state.
      await act(async () => { await useEditorStore.getState().recognize(); });
      expect(window.confirm).not.toHaveBeenCalled();
      expect(api.createRecognition).not.toHaveBeenCalled();
    },
  );

  it('a succeeded run never offers arbitrary recognition again', async () => {
    useEditorStore.setState({ recognitionRun: { ...failed, status: 'succeeded', can_retry: false },
      recognitionState: 'applied' });
    render(<Harness />);
    expect(screen.queryByRole('button', { name: /重试识别/ })).not.toBeInTheDocument();
    await act(async () => { await useEditorStore.getState().recognize(); });
    expect(api.createRecognition).not.toHaveBeenCalled();
  });
});

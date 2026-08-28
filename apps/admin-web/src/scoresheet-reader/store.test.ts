import { beforeEach, describe, expect, it, vi } from 'vitest';
import { setTablePersonnel } from '@pkuba/scoresheet-domain';
import { api } from './api';
import { useEditorStore } from './store';
import { makeDocument, makeTemplate } from './test/fixtures';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const emptyGamePage = {
  items: [], total: 0, page: 1, page_size: 20, division_names: [],
};

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  window.history.replaceState(null, '', '/scoresheet.html');
  vi.spyOn(api, 'changes').mockResolvedValue({ items: [], next_before_id: null });
  useEditorStore.setState({
    contextReviewing: false,
    document: makeDocument(),
    serverRevision: 0,
    template: makeTemplate(),
    games: [],
    gamesTotal: 0,
    gamesPage: 1,
    gamesPageSize: 20,
    gamesScope: 'ACTION_REQUIRED',
    gamesQuery: '',
    gamesLoading: false,
    recognitionMode: 'mock',
    validation: null,
    recognitionRun: null,
    recognitionDiff: null,
    recognitionState: 'idle',
    changes: [],
    selectedField: 'document',
    past: [],
    future: [],
    dirty: false,
    pendingSaveSource: 'human',
    saveState: 'saved',
    loading: false,
    error: '',
    readOnly: false,
    readOnlyReason: '',
    autoAcquireLease: true,
    online: true,
    leaseHolder: null,
    seasonId: '',
  });
});

describe('editor persistence and history', () => {
  it('reviews the bound context, preserves manual data and revalidates the returned revision', async () => {
    const initial = makeDocument('context-review');
    initial.header.crew_chief = '人工保留';
    const reviewed = structuredClone(initial);
    reviewed.revision = 1;
    reviewed.header.venue = '当前场地';
    useEditorStore.setState({ document: initial, validation: {
      status: 'invalid', checked_at: '2026-08-27T00:00:00Z', issues: [],
      game_context: { required: true, differences: [], player_conflicts: [], review_token: 'bound-review' },
    } });
    const pending = deferred<typeof reviewed>();
    const review = vi.spyOn(api, 'reviewGameContext').mockReturnValue(pending.promise);
    const validate = vi.spyOn(api, 'validate').mockResolvedValue({
      status: 'valid', checked_at: '2026-08-27T00:00:01Z', issues: [],
    });
    const operation = useEditorStore.getState().reviewGameContext([]);
    expect(useEditorStore.getState().contextReviewing).toBe(true);
    useEditorStore.getState().mutate((draft) => { draft.header.crew_chief = '不能中途覆盖'; });
    pending.resolve(reviewed);
    await operation;
    expect(review).toHaveBeenCalledWith(initial.id, 0, 'bound-review', []);
    expect(validate).toHaveBeenCalledWith(initial.id, 1);
    expect(useEditorStore.getState().document?.header.crew_chief).toBe('人工保留');
    expect(useEditorStore.getState().serverRevision).toBe(1);
    expect(useEditorStore.getState().contextReviewing).toBe(false);
  });

  it('does not lose the local document or confirm stale context after a failed review', async () => {
    const document = makeDocument('context-failed');
    useEditorStore.setState({ document, validation: {
      status: 'invalid', checked_at: '2026-08-27T00:00:00Z', issues: [],
      game_context: { required: true, differences: [], player_conflicts: [], review_token: 'stale-review' },
    } });
    const review = vi.spyOn(api, 'reviewGameContext').mockRejectedValue(new Error('请重新核对'));
    useEditorStore.setState({ dirty: true });
    await useEditorStore.getState().reviewGameContext([]);
    expect(review).not.toHaveBeenCalled();
    useEditorStore.setState({ dirty: false });
    await useEditorStore.getState().reviewGameContext([]);
    expect(useEditorStore.getState().document).toBe(document);
    expect(useEditorStore.getState().contextReviewing).toBe(false);
    expect(useEditorStore.getState().error).toBe('请重新核对');
  });

  it('keeps a manual table-personnel sentinel in draft status', () => {
    const document = { ...makeDocument('manual-personnel'), status: 'draft' as const };
    document.recognition = null;
    useEditorStore.setState({ document, serverRevision: 0 });

    useEditorStore.getState().mutate((draft) => {
      setTablePersonnel(draft, ['人工记录台人员']);
    });

    expect(useEditorStore.getState().document?.recognition?.run_id).toBe(
      'manual-table-personnel',
    );
    expect(useEditorStore.getState().document?.status).toBe('draft');
  });
  it('starts monitoring the backend recognition created by a game upload', async () => {
    const document = {
      ...makeDocument('auto-upload-document'),
      status: 'draft' as const,
      game_prior: {
        game_id: 'game-1', competition: '测试赛', division: '测试组', date: '2026-08-19',
        scheduled_time: '14:00', venue: '球馆', source_hash: 'hash', locked_paths: [],
        team_a: { team_id: 'a', name: '示例学院甲', player_names: ['甲队员一'] },
        team_b: { team_id: 'b', name: '示例学院乙', player_names: ['乙队员一'] },
      },
      source: { ...makeDocument().source, original_url: '/source.png', version: 0 },
    };
    const recognized = {
      ...structuredClone(document),
      revision: 1,
      status: 'needs_review' as const,
      recognition: {
        run_id: 'auto-run', notes: '', table_personnel: [], problem_paths: [],
        applied_at: '2026-08-21T00:00:00Z',
      },
    };
    const pending = {
      id: 'auto-run', document_id: document.id, base_revision: 0,
      status: 'pending' as const, model: 'qwen3.8-max', prompt_version: 'prompt',
      trigger: 'upload' as const, source_version: 0, image_sha256: 'image',
      superseded_by_run_id: null, retry_count: 0,
      cached: false, auto_applied: false, applied_revision: null,
      recognition_notes: '',
      usage: { input_tokens: 0, output_tokens: 0, image_tokens: 0, reasoning_tokens: 0, total_tokens: 0 },
      error: '', result: null, created_at: '2026-08-21T00:00:00Z', updated_at: '2026-08-21T00:00:00Z',
    };
    const succeeded = {
      ...pending,
      status: 'succeeded' as const,
      auto_applied: true,
      applied_revision: 1,
      result: {},
    };
    vi.spyOn(api, 'createGameDocument').mockResolvedValue({
      document,
      recognition_run: pending,
    });
    vi.spyOn(api, 'streamRecognition').mockResolvedValue(succeeded);
    vi.spyOn(api, 'document').mockResolvedValue(recognized);
    vi.spyOn(api, 'games').mockResolvedValue(emptyGamePage);
    const manualStart = vi.spyOn(api, 'createRecognition');

    await useEditorStore.getState().uploadForGame(
      'game-1',
      new File(['image'], 'sheet.png', { type: 'image/png' }),
    );
    await vi.waitFor(() => expect(useEditorStore.getState().recognitionState).toBe('applied'));

    expect(manualStart).not.toHaveBeenCalled();
    expect(useEditorStore.getState().document?.recognition?.run_id).toBe('auto-run');
  });

  it('reuploads through the replacement endpoint and immediately follows its new run', async () => {
    const current = {
      ...makeDocument('replace-document'),
      revision: 3,
      game_prior: {
        game_id: 'game-1', competition: '测试赛', division: '测试组', date: '2026-08-19',
        scheduled_time: '14:00', venue: '球馆', source_hash: 'hash', locked_paths: [],
        team_a: { team_id: 'a', name: '示例学院甲', player_names: [] },
        team_b: { team_id: 'b', name: '示例学院乙', player_names: [] },
      },
      source: { ...makeDocument().source, original_url: '/source.png', version: 0 },
    };
    const replacement = {
      ...structuredClone(current),
      revision: 4,
      status: 'draft' as const,
      recognition: null,
      source: { ...current.source, version: 1 },
    };
    const run = {
      id: 'replacement-run', document_id: current.id, base_revision: 4,
      status: 'failed' as const, model: 'qwen3.8-max', prompt_version: 'prompt',
      trigger: 'reupload' as const, source_version: 1, image_sha256: 'same-hash',
      superseded_by_run_id: null, retry_count: 0,
      cached: false, auto_applied: false, applied_revision: null,
      recognition_notes: '',
      usage: { input_tokens: 0, output_tokens: 0, image_tokens: 0, reasoning_tokens: 0, total_tokens: 0 },
      error: 'mock failure', result: null,
      created_at: '2026-08-21T00:00:00Z', updated_at: '2026-08-21T00:00:00Z',
    };
    useEditorStore.setState({ document: current, serverRevision: 3 });
    const replace = vi.spyOn(api, 'replaceDocumentSource').mockResolvedValue({
      document: replacement,
      recognition_run: run,
    });
    vi.spyOn(api, 'games').mockResolvedValue(emptyGamePage);

    const file = new File(['same image'], 'same.png', { type: 'image/png' });
    await useEditorStore.getState().reupload(current.id, file);
    await vi.waitFor(() => expect(useEditorStore.getState().recognitionState).toBe('failed'));

    expect(replace).toHaveBeenCalledWith(current.id, 3, file);
    expect(useEditorStore.getState().document?.source.version).toBe(1);
  });

  it('supports undo and redo with whole-document semantic snapshots', () => {
    useEditorStore.getState().mutate((draft) => {
      draft.header.competition = '修改后的比赛';
    });
    expect(useEditorStore.getState().document?.header.competition).toBe('修改后的比赛');
    expect(useEditorStore.getState().past).toHaveLength(1);

    useEditorStore.getState().undo();
    expect(useEditorStore.getState().document?.header.competition).toBe('合成测试赛');
    useEditorStore.getState().redo();
    expect(useEditorStore.getState().document?.header.competition).toBe('修改后的比赛');
  });

  it('blocks new edits and history actions while offline without discarding the draft', () => {
    const before = structuredClone(useEditorStore.getState().document);
    useEditorStore.setState({
      online: false,
      past: [makeDocument('offline-undo')],
      future: [makeDocument('offline-redo')],
    });

    useEditorStore.getState().mutate((draft) => {
      draft.header.competition = '不应写入';
    });
    useEditorStore.getState().undo();
    useEditorStore.getState().redo();

    expect(useEditorStore.getState().document).toEqual(before);
    expect(useEditorStore.getState().dirty).toBe(false);
    expect(useEditorStore.getState().error).toContain('网络已断开');
  });

  it('opens with a blank template when no real document can be restored', async () => {
    vi.spyOn(api, 'template').mockResolvedValue(makeTemplate());
    vi.spyOn(api, 'games').mockResolvedValue(emptyGamePage);
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', recognition: 'mock', master_data: 'ready' });
    useEditorStore.setState({ document: null, template: null, loading: true });

    await useEditorStore.getState().initialize();
    expect(useEditorStore.getState().document).toBeNull();
    expect(useEditorStore.getState().template).toEqual(makeTemplate());
    expect(useEditorStore.getState().saveState).toBe('idle');
  });

  it('restores the last real game document', async () => {
    const document = makeDocument('real-document');
    document.game_prior = {
      game_id: 'game-1', competition: '正式比赛', division: '男篮', date: '2026-08-21',
      scheduled_time: '19:00', venue: '体育馆', source_hash: 'hash', locked_paths: [],
      team_a: { team_id: 'a', name: '甲队', player_names: [] },
      team_b: { team_id: 'b', name: '乙队', player_names: [] },
    };
    document.source.original_url = '/api/v1/documents/real-document/source';
    localStorage.setItem('scoresheet-reader:last-document-id', document.id);
    vi.spyOn(api, 'template').mockResolvedValue(makeTemplate());
    vi.spyOn(api, 'games').mockResolvedValue(emptyGamePage);
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', recognition: 'mock', master_data: 'ready' });
    vi.spyOn(api, 'document').mockResolvedValue(document);
    vi.spyOn(api, 'latestRecognition').mockResolvedValue(null);
    useEditorStore.setState({ document: null, template: null, loading: true });

    await useEditorStore.getState().initialize();

    expect(useEditorStore.getState().document?.id).toBe('real-document');
    expect(useEditorStore.getState().saveState).toBe('saved');
  });

  it('prioritizes the season and game supplied by the competition media deep link', async () => {
    const document = makeDocument('linked-document');
    document.game_prior = {
      game_id: 'linked-game', competition: '正式比赛', division: '女篮', date: '2026-08-22',
      scheduled_time: '20:00', venue: '第一体育馆', source_hash: 'hash', locked_paths: [],
      team_a: { team_id: 'a', name: '物院', player_names: [] },
      team_b: { team_id: 'b', name: '化院', player_names: [] },
    };
    document.source.original_url = '/api/v1/documents/linked-document/source';
    localStorage.setItem('scoresheet-reader:last-document-id', 'unrelated-document');
    window.history.replaceState(null, '', '/scoresheet.html?season_id=season-2026&game_id=linked-game');
    vi.spyOn(api, 'template').mockResolvedValue(makeTemplate());
    const gamesSpy = vi.spyOn(api, 'games').mockResolvedValue(emptyGamePage);
    vi.spyOn(api, 'game').mockResolvedValue({
      id: 'linked-game', competition: '正式比赛', division: '女篮', date: '2026-08-22',
      scheduled_time: '20:00', venue: '第一体育馆', team_a_name: '物院', team_b_name: '化院',
      ready: true, unavailable_reason: '', document_id: 'linked-document', scoresheet_state: 'recognized',
      prior: document.game_prior,
      can_upload_source: true,
    });
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', recognition: 'mock', master_data: 'ready' });
    const documentSpy = vi.spyOn(api, 'document').mockResolvedValue(document);
    vi.spyOn(api, 'latestRecognition').mockResolvedValue(null);
    useEditorStore.setState({ document: null, template: null, loading: true });

    await useEditorStore.getState().initialize();

    expect(gamesSpy).toHaveBeenCalledWith({
      seasonId: 'season-2026', scope: 'ACTION_REQUIRED', page: 1, pageSize: 20,
    });
    expect(documentSpy).toHaveBeenCalledWith('linked-document', expect.any(Function));
    expect(useEditorStore.getState().document?.id).toBe('linked-document');
    expect(useEditorStore.getState().seasonId).toBe('season-2026');
  });

  it('clears an old synthetic last-document id and shows the blank template', async () => {
    const legacy = makeDocument('synthetic-preview');
    localStorage.setItem('scoresheet-reader:last-document-id', legacy.id);
    vi.spyOn(api, 'template').mockResolvedValue(makeTemplate());
    vi.spyOn(api, 'games').mockResolvedValue(emptyGamePage);
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', recognition: 'mock', master_data: 'ready' });
    vi.spyOn(api, 'document').mockResolvedValue(legacy);
    useEditorStore.setState({ document: null, template: null, loading: true });

    await useEditorStore.getState().initialize();

    expect(useEditorStore.getState().document).toBeNull();
    expect(localStorage.getItem('scoresheet-reader:last-document-id')).toBeNull();
  });

  it('turns a stale backend revision into a visible conflict state', async () => {
    const document = makeDocument('persisted-document');
    useEditorStore.setState({ document, dirty: true });
    const conflict = Object.assign(new Error('草稿已被更新'), { status: 409 });
    vi.spyOn(api, 'save').mockRejectedValue(conflict);

    await useEditorStore.getState().save();
    expect(useEditorStore.getState().saveState).toBe('conflict');
    expect(useEditorStore.getState().error).toBe('草稿已被更新');
  });

  it('can explicitly rebase the local draft after a revision conflict', async () => {
    const local = makeDocument('persisted-document');
    local.header.game_number = '本地内容';
    const latest = {
      ...structuredClone(local),
      revision: 5,
      header: { ...local.header, game_number: '服务器内容' },
      source: { ...local.source, original_filename: 'server-owned.png' },
    };
    useEditorStore.setState({
      document: local,
      serverRevision: 0,
      dirty: true,
      saveState: 'conflict',
    });
    vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    vi.spyOn(api, 'document').mockResolvedValue(latest);
    const save = vi.spyOn(api, 'save').mockImplementation(async (candidate, baseRevision) => ({
      ...structuredClone(candidate),
      revision: baseRevision + 1,
    }));

    await useEditorStore.getState().overwriteAfterConflict();

    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({
        header: expect.objectContaining({ game_number: '本地内容' }),
        source: expect.objectContaining({ original_filename: 'server-owned.png' }),
      }),
      5,
      'human',
    );
    expect(useEditorStore.getState().serverRevision).toBe(6);
    expect(useEditorStore.getState().saveState).toBe('saved');
  });

  it('does not overwrite edits made while a save request is in flight', async () => {
    const document = makeDocument('persisted-document');
    useEditorStore.setState({ document, dirty: true, saveState: 'dirty' });
    const pending = deferred<ReturnType<typeof makeDocument>>();
    vi.spyOn(api, 'save').mockReturnValue(pending.promise);

    const saving = useEditorStore.getState().save();
    await vi.waitFor(() => expect(api.save).toHaveBeenCalledOnce());
    useEditorStore.getState().mutate((draft) => {
      draft.header.game_number = '保存期间的新修改';
    });
    pending.resolve({ ...structuredClone(document), revision: 1 });
    await saving;

    expect(useEditorStore.getState().document?.header.game_number).toBe('保存期间的新修改');
    expect(useEditorStore.getState().serverRevision).toBe(1);
    expect(useEditorStore.getState().document?.revision).toBe(1);
    expect(useEditorStore.getState().dirty).toBe(true);
    expect(useEditorStore.getState().saveState).toBe('dirty');
  });

  it('never validates or submits stale server data when pending edits fail to save', async () => {
    const document = makeDocument('persisted-document');
    useEditorStore.setState({ document, dirty: true, saveState: 'dirty' });
    vi.spyOn(api, 'save').mockRejectedValue(new Error('磁盘写入失败'));
    const validate = vi.spyOn(api, 'validate');

    const report = await useEditorStore.getState().validate();

    expect(report).toBeNull();
    expect(validate).not.toHaveBeenCalled();
    expect(useEditorStore.getState().dirty).toBe(true);
    expect(useEditorStore.getState().error).toBe('草稿尚未成功保存，已停止校验和提交。');
  });

  it('discards a validation result when the document changes during validation', async () => {
    const document = makeDocument('persisted-document');
    useEditorStore.setState({ document, serverRevision: 3 });
    const pending = deferred<{ status: 'valid'; issues: []; checked_at: string }>();
    vi.spyOn(api, 'validate').mockReturnValue(pending.promise);

    const validating = useEditorStore.getState().validate();
    await vi.waitFor(() => expect(api.validate).toHaveBeenCalledWith(document.id, 3));
    useEditorStore.getState().mutate((draft) => {
      draft.header.game_number = '校验期间修改';
    });
    pending.resolve({ status: 'valid', issues: [], checked_at: '2026-08-21T00:00:00Z' });

    expect(await validating).toBeNull();
    expect(useEditorStore.getState().validation).toBeNull();
    expect(useEditorStore.getState().error).toContain('旧校验结果已丢弃');
  });

  it('keeps edits made while confirmation is in flight and requires resubmission', async () => {
    const document = makeDocument('persisted-document');
    useEditorStore.setState({ document, serverRevision: 0 });
    vi.spyOn(api, 'validate').mockResolvedValue({
      status: 'valid', issues: [], checked_at: '2026-08-21T00:00:00Z',
    });
    const pending = deferred<ReturnType<typeof makeDocument>>();
    vi.spyOn(api, 'confirm').mockReturnValue(pending.promise);
    vi.spyOn(globalThis, 'confirm').mockReturnValue(true);

    const confirming = useEditorStore.getState().confirm();
    await vi.waitFor(() => expect(api.confirm).toHaveBeenCalledOnce());
    useEditorStore.getState().mutate((draft) => {
      draft.header.game_number = '提交期间修改';
    });
    pending.resolve({ ...structuredClone(document), revision: 1, status: 'confirmed' });
    await confirming;

    expect(useEditorStore.getState().document?.header.game_number).toBe('提交期间修改');
    expect(useEditorStore.getState().document?.status).toBe('draft');
    expect(useEditorStore.getState().serverRevision).toBe(1);
    expect(useEditorStore.getState().dirty).toBe(true);
    expect(useEditorStore.getState().error).toContain('需要重新保存、校验并提交');
  });

  it('does not reacquire an edit lease in the background after publication', async () => {
    const document = makeDocument('published-document');
    const confirmed = { ...structuredClone(document), revision: 1, status: 'confirmed' as const };
    useEditorStore.setState({ document, serverRevision: 0 });
    vi.spyOn(api, 'validate').mockResolvedValue({
      status: 'valid', issues: [], checked_at: '2026-08-21T00:00:00Z',
    });
    vi.spyOn(api, 'confirm').mockResolvedValue(confirmed);
    vi.spyOn(api, 'games').mockResolvedValue(emptyGamePage);
    vi.spyOn(api, 'leaseState').mockReturnValue(null);
    vi.spyOn(api, 'sync').mockResolvedValue({
      current_version: 1,
      can_upload_source: false,
      current_event: 1,
      requires_full_reload: false,
      events: [],
      lease: null,
      recognition: null,
    });
    const acquire = vi.spyOn(api, 'acquire');
    vi.spyOn(globalThis, 'confirm').mockReturnValue(true);

    await useEditorStore.getState().confirm();
    await useEditorStore.getState().syncNow();

    expect(useEditorStore.getState().document?.status).toBe('confirmed');
    expect(useEditorStore.getState().autoAcquireLease).toBe(false);
    expect(useEditorStore.getState().readOnly).toBe(true);
    expect(useEditorStore.getState().readOnlyReason).toContain('已发布');
    expect(acquire).not.toHaveBeenCalled();
  });

  it('does not immediately reclaim a deliberately released edit lease', async () => {
    vi.spyOn(api, 'release').mockResolvedValue();
    vi.spyOn(api, 'heartbeat').mockResolvedValue(null);
    const acquire = vi.spyOn(api, 'acquire');

    await useEditorStore.getState().releaseLease();
    await useEditorStore.getState().heartbeatLease();

    expect(useEditorStore.getState().autoAcquireLease).toBe(false);
    expect(useEditorStore.getState().readOnlyReason).toBe('编辑权已释放。');
    expect(acquire).not.toHaveBeenCalled();
  });

  it('rebases an undo snapshot onto the current server revision before saving', async () => {
    const document = { ...makeDocument('persisted-document'), revision: 5 };
    useEditorStore.setState({ document, serverRevision: 5 });
    const save = vi.spyOn(api, 'save')
      .mockImplementation(async (candidate, baseRevision) => ({
        ...structuredClone(candidate),
        revision: baseRevision + 1,
      }));

    useEditorStore.getState().mutate((draft) => { draft.header.game_number = '123'; });
    await useEditorStore.getState().save();
    expect(useEditorStore.getState().serverRevision).toBe(6);
    useEditorStore.getState().undo();
    expect(useEditorStore.getState().document?.revision).toBe(6);
    await useEditorStore.getState().save();

    expect(save).toHaveBeenLastCalledWith(
      expect.objectContaining({ revision: 6 }),
      6,
      'undo',
    );
    expect(useEditorStore.getState().saveState).toBe('saved');
  });

  it('opens a recognized game document and restores its model run', async () => {
    const document = {
      ...makeDocument('recognized-from-schedule'),
      revision: 4,
      recognition: {
        run_id: 'saved-run',
        notes: '一处号码待核对',
        table_personnel: ['示例记录台人员'],
        problem_paths: ['/teams/0/players/1/jersey_number'],
        applied_at: '2026-08-19T00:00:00Z',
      },
    };
    const run = {
      id: 'saved-run', document_id: document.id, base_revision: 0,
      status: 'succeeded' as const, model: 'qwen3.8-max', cached: false,
      prompt_version: 'scoresheet-2026-08-19-v4',
      auto_applied: true, applied_revision: 1, recognition_notes: '一处号码待核对',
      usage: { input_tokens: 6449, output_tokens: 3602, image_tokens: 2122, reasoning_tokens: 0, total_tokens: 10051 },
      error: '', result: {}, created_at: '2026-08-19T00:00:00Z',
      updated_at: '2026-08-19T00:00:00Z',
    };
    vi.spyOn(api, 'document').mockResolvedValue(document);
    vi.spyOn(api, 'latestRecognition').mockResolvedValue(run);

    await useEditorStore.getState().openDocument(document.id);

    expect(useEditorStore.getState().document?.id).toBe(document.id);
    expect(useEditorStore.getState().serverRevision).toBe(4);
    expect(useEditorStore.getState().recognitionRun?.usage.total_tokens).toBe(10051);
    expect(useEditorStore.getState().recognitionState).toBe('applied');
    expect(localStorage.getItem('scoresheet-reader:last-document-id')).toBe(document.id);
  });

  it('loads an automatically applied zero-token recognition into the editor history', async () => {
    const original = {
      ...makeDocument('recognized-document'),
      revision: 0,
      game_prior: {
        game_id: 'game-1', competition: '测试赛', division: '测试组', date: '2026-08-19',
        scheduled_time: '14:00', venue: '球馆', source_hash: 'hash', locked_paths: [],
        team_a: { team_id: 'a', name: '示例学院甲', player_names: ['甲队员一'] },
        team_b: { team_id: 'b', name: '示例学院乙', player_names: ['乙队员一'] },
      },
      source: { ...makeDocument().source, original_url: '/source.png' },
    };
    const recognized = {
      ...structuredClone(original),
      revision: 1,
      recognition: {
        run_id: 'run-1', notes: '', table_personnel: [], problem_paths: [],
        applied_at: '2026-08-19T00:00:00Z',
      },
    };
    const run = {
      id: 'run-1', document_id: original.id, base_revision: 0, status: 'succeeded' as const,
      model: 'qwen3.8-max', cached: false, auto_applied: true, applied_revision: 1,
      prompt_version: 'scoresheet-2026-08-19-v4',
      recognition_notes: '', usage: { input_tokens: 0, output_tokens: 0, image_tokens: 0, reasoning_tokens: 0, total_tokens: 0 },
      error: '', result: {}, created_at: '2026-08-19T00:00:00Z', updated_at: '2026-08-19T00:00:00Z',
    };
    useEditorStore.setState({ document: original, serverRevision: 0,
      recognitionRun: { ...run, status: 'failed', can_retry: true }, recognitionState: 'failed' });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.spyOn(api, 'createRecognition').mockResolvedValue(run);
    vi.spyOn(api, 'document').mockResolvedValue(recognized);

    await useEditorStore.getState().recognize();

    expect(useEditorStore.getState().document?.revision).toBe(1);
    expect(useEditorStore.getState().serverRevision).toBe(1);
    expect(useEditorStore.getState().recognitionRun?.usage.total_tokens).toBe(0);
    expect(useEditorStore.getState().recognitionState).toBe('applied');
    expect(useEditorStore.getState().past).toHaveLength(1);
  });

  it('never applies a recognition update to a different open document', async () => {
    const original = {
      ...makeDocument('recognition-target'),
      game_prior: {
        game_id: 'game-1', competition: '测试赛', division: '测试组', date: '2026-08-19',
        scheduled_time: '14:00', venue: '球馆', source_hash: 'hash', locked_paths: [],
        team_a: { team_id: 'a', name: '示例学院甲', player_names: ['甲队员一'] },
        team_b: { team_id: 'b', name: '示例学院乙', player_names: ['乙队员一'] },
      },
      source: { ...makeDocument().source, original_url: '/source.png' },
    };
    const run = {
      id: 'run-target', document_id: original.id, base_revision: 0,
      status: 'pending' as const, model: 'qwen3.8-max', cached: false,
      prompt_version: 'prompt', auto_applied: false, applied_revision: null,
      recognition_notes: '',
      usage: { input_tokens: 0, output_tokens: 0, image_tokens: 0, reasoning_tokens: 0, total_tokens: 0 },
      error: '', result: null, created_at: '2026-08-21T00:00:00Z', updated_at: '2026-08-21T00:00:00Z',
    };
    const terminal = { ...run, status: 'succeeded' as const, result: {} };
    const pending = deferred<typeof terminal>();
    useEditorStore.setState({ document: original, serverRevision: 0,
      recognitionRun: { ...run, status: 'failed', can_retry: true }, recognitionState: 'failed' });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    vi.spyOn(api, 'createRecognition').mockResolvedValue(run);
    vi.spyOn(api, 'streamRecognition').mockReturnValue(pending.promise);
    const diff = vi.spyOn(api, 'recognitionDiff');

    const recognizing = useEditorStore.getState().recognize();
    await vi.waitFor(() => expect(api.streamRecognition).toHaveBeenCalledOnce());
    const other = makeDocument('other-document');
    useEditorStore.setState({
      document: other,
      serverRevision: 0,
      recognitionRun: null,
      recognitionState: 'idle',
    });
    pending.resolve(terminal);
    await recognizing;

    expect(useEditorStore.getState().document?.id).toBe('other-document');
    expect(useEditorStore.getState().recognitionRun).toBeNull();
    expect(diff).not.toHaveBeenCalled();
  });

  it('does not revive an obsolete watcher after navigating away and back', async () => {
    const target = makeDocument('watch-target');
    const other = makeDocument('watch-other');
    const run = {
      id: 'watch-run', document_id: target.id, base_revision: 0,
      status: 'pending' as const, model: 'qwen3.8-max', prompt_version: 'prompt',
      cached: false, auto_applied: false, applied_revision: null,
      recognition_notes: '',
      usage: { input_tokens: 0, output_tokens: 0, image_tokens: 0, reasoning_tokens: 0, total_tokens: 0 },
      error: '', result: null, created_at: '2026-08-21T00:00:00Z', updated_at: '2026-08-21T00:00:00Z',
    };
    const terminal = {
      ...run,
      status: 'succeeded' as const,
      auto_applied: true,
      applied_revision: 1,
      result: {},
    };
    const pending = deferred<typeof terminal>();
    useEditorStore.setState({
      document: target,
      serverRevision: 0,
      recognitionRun: run,
      recognitionState: 'running',
    });
    vi.spyOn(api, 'streamRecognition').mockReturnValue(pending.promise);
    vi.spyOn(api, 'document').mockImplementation(async (id) => (
      id === other.id ? other : target
    ));
    vi.spyOn(api, 'latestRecognition').mockResolvedValue(null);

    const watching = useEditorStore.getState().watchRecognition(run, target);
    await vi.waitFor(() => expect(api.streamRecognition).toHaveBeenCalledOnce());
    await useEditorStore.getState().openDocument(other.id);
    await useEditorStore.getState().openDocument(target.id);
    pending.resolve(terminal);
    await watching;

    expect(useEditorStore.getState().document?.id).toBe(target.id);
    expect(useEditorStore.getState().document?.recognition).toBeFalsy();
    expect(useEditorStore.getState().recognitionRun).toBeNull();
  });

  it('cancels document navigation when the dirty draft cannot be saved', async () => {
    const document = makeDocument('dirty-document');
    useEditorStore.setState({ document, dirty: true, saveState: 'dirty' });
    vi.spyOn(api, 'save').mockRejectedValue(new Error('保存失败'));
    const open = vi.spyOn(api, 'document');

    await expect(useEditorStore.getState().openDocument('other-document')).rejects.toThrow(
      '当前草稿尚未保存',
    );

    expect(open).not.toHaveBeenCalled();
    expect(useEditorStore.getState().document?.id).toBe('dirty-document');
  });
});

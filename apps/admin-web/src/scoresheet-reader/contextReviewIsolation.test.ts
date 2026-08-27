import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
import { useEditorStore } from './store';
import { makeDocument } from './test/fixtures';
import type { DocumentChangeLogPage, ScoresheetDocument, ValidationReport } from './types';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((accept, fail) => { resolve = accept; reject = fail; });
  return { promise, resolve, reject };
}

const valid: ValidationReport = {
  status: 'valid', checked_at: '2026-08-27T00:00:00Z', issues: [],
};
const emptyChanges: DocumentChangeLogPage = { items: [], next_before_id: null };
let documents: Map<string, ScoresheetDocument>;

function target(id: string, revision = 0) {
  const document = makeDocument(id);
  document.revision = revision;
  document.header.crew_chief = `${id} 人工内容`;
  documents.set(id, document);
  return document;
}

function requireReview() {
  useEditorStore.setState({
    validation: {
      ...valid,
      status: 'invalid',
      game_context: {
        required: true, differences: [], player_conflicts: [],
        review_token: `fixture-review-${useEditorStore.getState().document!.id}`,
      },
    },
  });
}

function snapshot() {
  const state = useEditorStore.getState();
  return structuredClone({
    document: state.document, revision: state.serverRevision, validation: state.validation,
    error: state.error, contextReviewing: state.contextReviewing, changes: state.changes,
    dirty: state.dirty, past: state.past, future: state.future, saveState: state.saveState,
    loading: state.loading, readOnly: state.readOnly,
  });
}

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  documents = new Map();
  const document = target('review-a');
  useEditorStore.setState({
    ...useEditorStore.getInitialState(), document, serverRevision: 0, loading: false,
    saveState: 'saved', readOnly: false, online: true,
  });
  requireReview();
  vi.spyOn(api, 'document').mockImplementation(async (id) => documents.get(id)!);
  vi.spyOn(api, 'latestRecognition').mockResolvedValue(null);
  vi.spyOn(api, 'release').mockResolvedValue(undefined);
  vi.spyOn(api, 'leaseState').mockReturnValue({
    token: 'fixture-token', readOnly: false, reason: '', holder: null, event: 0,
  });
  vi.spyOn(api, 'changes').mockResolvedValue(emptyChanges);
  vi.spyOn(api, 'validate').mockResolvedValue(valid);
});

describe('game-context review belongs to one open record session', () => {
  it('discards a previous record review failure without changing the new record', async () => {
    const pending = deferred<ScoresheetDocument>();
    vi.spyOn(api, 'reviewGameContext').mockReturnValue(pending.promise);
    const reviewing = useEditorStore.getState().reviewGameContext([]);
    const other = target('review-b', 7);
    await useEditorStore.getState().openDocument(other.id);
    const before = snapshot();
    pending.reject(new Error('旧 A 请求失败'));
    await reviewing;
    expect(snapshot()).toEqual(before);
    expect(useEditorStore.getState().document).toBe(other);
    expect(api.validate).not.toHaveBeenCalled();
  });

  it.each(['success', 'failure'] as const)(
    'does not revive an old review after A to B to A (%s)', async (outcome) => {
      const original = useEditorStore.getState().document!;
      const pending = deferred<ScoresheetDocument>();
      vi.spyOn(api, 'reviewGameContext').mockReturnValue(pending.promise);
      const reviewing = useEditorStore.getState().reviewGameContext([]);
      await useEditorStore.getState().openDocument(target('review-b', 4).id);
      const current = target(original.id, 10);
      await useEditorStore.getState().openDocument(current.id);
      const before = snapshot();
      const historyCalls = vi.mocked(api.changes).mock.calls.length;
      if (outcome === 'success') pending.resolve({ ...original, revision: 1 });
      else pending.reject(new Error('旧 A 失败'));
      await reviewing;
      expect(snapshot()).toEqual(before);
      expect(useEditorStore.getState().document).toBe(current);
      expect(api.validate).not.toHaveBeenCalled();
      expect(api.changes).toHaveBeenCalledTimes(historyCalls);
    },
  );

  it('invalidates a review as soon as an accepted navigation starts loading', async () => {
    const original = useEditorStore.getState().document!;
    const pending = deferred<ScoresheetDocument>();
    vi.spyOn(api, 'reviewGameContext').mockReturnValue(pending.promise);
    const reviewing = useEditorStore.getState().reviewGameContext([]);
    const other = target('review-b', 7);
    const opening = deferred<ScoresheetDocument>();
    vi.mocked(api.document).mockReturnValueOnce(opening.promise);
    const navigating = useEditorStore.getState().openDocument(other.id);
    await vi.waitFor(() => expect(api.document).toHaveBeenCalledWith(other.id));
    pending.resolve({ ...original, revision: 1 });
    await reviewing;
    const validateCalls = vi.mocked(api.validate).mock.calls.length;
    opening.resolve(other);
    await navigating;
    expect(validateCalls).toBe(0);
    expect(useEditorStore.getState().document).toBe(other);
    expect(useEditorStore.getState().contextReviewing).toBe(false);
  });

  it('does not clear a newer record review busy state when the old operation settles', async () => {
    const original = useEditorStore.getState().document!;
    const oldReview = deferred<ScoresheetDocument>();
    const newReview = deferred<ScoresheetDocument>();
    const review = vi.spyOn(api, 'reviewGameContext')
      .mockReturnValueOnce(oldReview.promise).mockReturnValueOnce(newReview.promise);
    const first = useEditorStore.getState().reviewGameContext([]);
    const other = target('review-b', 7);
    await useEditorStore.getState().openDocument(other.id);
    requireReview();
    const second = useEditorStore.getState().reviewGameContext([]);
    const started = review.mock.calls.length;
    oldReview.resolve({ ...original, revision: 1 });
    await first;
    const stateDuringSecond = snapshot();
    newReview.resolve({ ...other, revision: 8 });
    await second;
    expect(started).toBe(2);
    expect(stateDuringSecond.contextReviewing).toBe(true);
    expect(stateDuringSecond.document?.id).toBe(other.id);
    expect(stateDuringSecond.revision).toBe(7);
    expect(stateDuringSecond.error).toBe('');
    expect(api.validate).toHaveBeenCalledExactlyOnceWith(other.id, 8);
  });

  it.each(['success', 'failure'] as const)(
    'does not publish old post-review validation into a new record (%s)', async (outcome) => {
      const original = useEditorStore.getState().document!;
      vi.spyOn(api, 'reviewGameContext').mockResolvedValue({ ...original, revision: 1 });
      const validating = deferred<ValidationReport>();
      vi.mocked(api.validate).mockReturnValueOnce(validating.promise);
      const reviewing = useEditorStore.getState().reviewGameContext([]);
      await vi.waitFor(() => expect(api.validate).toHaveBeenCalledWith(original.id, 1));
      await useEditorStore.getState().openDocument(target('review-b', 7).id);
      const before = snapshot();
      const historyCalls = vi.mocked(api.changes).mock.calls.length;
      if (outcome === 'success') validating.resolve(valid);
      else validating.reject(new Error('旧 A 校验失败'));
      await reviewing;
      expect(snapshot()).toEqual(before);
      expect(api.changes).toHaveBeenCalledTimes(historyCalls);
    },
  );

  it('preserves a current same-name record after reloading its source during a review', async () => {
    const original = useEditorStore.getState().document!;
    const pending = deferred<ScoresheetDocument>();
    vi.spyOn(api, 'reviewGameContext').mockReturnValue(pending.promise);
    const reviewing = useEditorStore.getState().reviewGameContext([]);
    const current = target(original.id, 10);
    current.source = { ...current.source, version: 2, original_url: '/new-source.jpg' };
    await useEditorStore.getState().openDocument(current.id);
    pending.resolve({ ...original, revision: 1 });
    await reviewing;
    expect(useEditorStore.getState().document).toBe(current);
    expect(useEditorStore.getState().serverRevision).toBe(10);
    expect(api.validate).not.toHaveBeenCalled();
  });
});

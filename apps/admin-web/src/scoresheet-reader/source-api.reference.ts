import type {
  DocumentChangeLogPage,
  DocumentRecognitionResponse,
  GameDetail,
  GameSummary,
  RecognitionDiff,
  RecognitionRun,
  ScoresheetDocument,
  TemplateDefinition,
  ValidationReport,
} from './types';

const jsonHeaders = { 'Content-Type': 'application/json' };

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }));
    const error = new Error(
      typeof payload.detail === 'string'
        ? payload.detail
        : payload.detail?.message ?? `请求失败：${response.status}`,
    );
    Object.assign(error, { status: response.status, payload });
    throw error;
  }
  return response.json() as Promise<T>;
}

export const api = {
  async health(): Promise<{ status: string; recognition: string; master_data: string }> {
    return parseResponse(await fetch('/api/v1/health'));
  },

  async template(): Promise<TemplateDefinition> {
    return parseResponse(await fetch('/api/v1/template/definition'));
  },

  async games(): Promise<GameSummary[]> {
    return parseResponse(await fetch('/api/v1/games'));
  },

  async game(id: string): Promise<GameDetail> {
    return parseResponse(await fetch(`/api/v1/games/${id}`));
  },

  async createGameDocument(gameId: string, file: File): Promise<DocumentRecognitionResponse> {
    const form = new FormData();
    form.append('file', file);
    return parseResponse(
      await fetch(`/api/v1/games/${gameId}/documents`, {
        method: 'POST',
        body: form,
      }),
    );
  },

  async replaceDocumentSource(
    documentId: string,
    baseRevision: number,
    file: File,
  ): Promise<DocumentRecognitionResponse> {
    const form = new FormData();
    form.append('file', file);
    form.append('base_revision', String(baseRevision));
    return parseResponse(
      await fetch(`/api/v1/documents/${documentId}/source`, {
        method: 'PUT',
        body: form,
      }),
    );
  },

  async document(id: string): Promise<ScoresheetDocument> {
    return parseResponse(await fetch(`/api/v1/documents/${id}`));
  },

  async save(
    document: ScoresheetDocument,
    baseRevision: number,
    source: 'human' | 'undo' | 'redo' = 'human',
  ): Promise<ScoresheetDocument> {
    return parseResponse(
      await fetch(`/api/v1/documents/${document.id}`, {
        method: 'PATCH',
        headers: jsonHeaders,
        body: JSON.stringify({ base_revision: baseRevision, document, source }),
      }),
    );
  },

  async align(
    document: ScoresheetDocument,
    baseRevision: number,
    rotation: 0 | 90 | 180 | 270,
    corners: number[][] | null,
  ): Promise<ScoresheetDocument> {
    return parseResponse(
      await fetch(`/api/v1/documents/${document.id}/alignment`, {
        method: 'POST',
        headers: jsonHeaders,
        body: JSON.stringify({
          base_revision: baseRevision,
          rotation,
          corners,
        }),
      }),
    );
  },

  async validate(id: string, baseRevision: number): Promise<ValidationReport> {
    return parseResponse(
      await fetch(`/api/v1/documents/${id}/validate`, {
        method: 'POST',
        headers: jsonHeaders,
        body: JSON.stringify({ base_revision: baseRevision }),
      }),
    );
  },

  async confirm(
    document: ScoresheetDocument,
    baseRevision: number,
    warningCodes: string[],
  ): Promise<ScoresheetDocument> {
    return parseResponse(
      await fetch(`/api/v1/documents/${document.id}/confirm`, {
        method: 'POST',
        headers: jsonHeaders,
        body: JSON.stringify({
          base_revision: baseRevision,
          acknowledge_warning_codes: warningCodes,
        }),
      }),
    );
  },

  async changes(
    id: string,
    limit = 50,
    beforeId?: number,
  ): Promise<DocumentChangeLogPage> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (beforeId !== undefined) params.set('before_id', String(beforeId));
    return parseResponse(await fetch(`/api/v1/documents/${id}/changes?${params}`));
  },

  async createRecognition(id: string, baseRevision: number): Promise<RecognitionRun> {
    return parseResponse(
      await fetch(`/api/v1/documents/${id}/recognitions`, {
        method: 'POST',
        headers: jsonHeaders,
        body: JSON.stringify({ base_revision: baseRevision }),
      }),
    );
  },

  async recognition(runId: string): Promise<RecognitionRun> {
    return parseResponse(await fetch(`/api/v1/recognitions/${runId}`));
  },

  async latestRecognition(documentId: string): Promise<RecognitionRun | null> {
    return parseResponse(
      await fetch(`/api/v1/documents/${documentId}/recognitions/latest`),
    );
  },

  async streamRecognition(
    runId: string,
    onUpdate: (run: RecognitionRun) => void,
  ): Promise<RecognitionRun> {
    if (typeof EventSource === 'undefined') {
      throw new Error('当前浏览器不支持识别进度流。');
    }
    return new Promise<RecognitionRun>((resolve, reject) => {
      const source = new EventSource(`/api/v1/recognitions/${runId}/events`);
      let settled = false;
      source.onmessage = (event) => {
        try {
          const run = JSON.parse(event.data) as RecognitionRun;
          onUpdate(run);
          if (['succeeded', 'failed', 'superseded', 'interrupted'].includes(run.status)) {
            settled = true;
            source.close();
            resolve(run);
          }
        } catch (error) {
          settled = true;
          source.close();
          reject(error);
        }
      };
      source.onerror = () => {
        source.close();
        if (!settled) reject(new Error('识别进度流已中断。'));
      };
    });
  },

  async recognitionDiff(runId: string): Promise<RecognitionDiff> {
    return parseResponse(await fetch(`/api/v1/recognitions/${runId}/diff`));
  },

  async applyRecognition(
    runId: string,
    baseRevision: number,
    regions: string[],
  ): Promise<ScoresheetDocument> {
    return parseResponse(
      await fetch(`/api/v1/recognitions/${runId}/apply`, {
        method: 'POST',
        headers: jsonHeaders,
        body: JSON.stringify({ base_revision: baseRevision, regions }),
      }),
    );
  },
};

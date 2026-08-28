import {
  ArrowLeft,
  CheckCircle2,
  Download,
  ListFilter,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  RotateCcw,
  RotateCw,
  Save,
  ScanLine,
  ShieldCheck,
} from 'lucide-react';
import type { ScoresheetDocument, ValidationReport } from '../types';
import { ScoresheetLogo } from './ScoresheetLogo';

interface TopBarProps {
  document: ScoresheetDocument | null;
  validation: ValidationReport | null;
  saveState: 'idle' | 'dirty' | 'saving' | 'saved' | 'conflict' | 'error';
  canUndo: boolean;
  canRedo: boolean;
  recognitionMode: string;
  recognitionState: 'idle' | 'starting' | 'running' | 'diff' | 'applied' | 'failed';
  recognitionRetryAllowed?: boolean;
  onChooseGame: () => void;
  onRecognize: () => Promise<void>;
  onUndo: () => void;
  onRedo: () => void;
  onSave: () => Promise<void>;
  onValidate: () => Promise<unknown>;
  onConfirm: () => Promise<void>;
  publicationRecoveryAllowed?: boolean;
  sourceOpen: boolean;
  inspectorOpen: boolean;
  onToggleSource: () => void;
  onToggleInspector: () => void;
  readOnly?: boolean;
  readOnlyReason?: string;
  online?: boolean;
  leaseHolder?: { username: string; surface: 'WEB' | 'MINIAPP' } | null;
  onReturn: () => void;
}

const saveLabels = {
  idle: '未保存',
  dirty: '等待保存',
  saving: '正在保存',
  saved: '已保存',
  conflict: '保存冲突',
  error: '保存失败',
};

export function TopBar({
  document,
  validation,
  saveState,
  canUndo,
  canRedo,
  recognitionMode,
  recognitionState,
  recognitionRetryAllowed = false,
  onChooseGame,
  onRecognize,
  onUndo,
  onRedo,
  onSave,
  onValidate,
  onConfirm,
  publicationRecoveryAllowed = false,
  sourceOpen,
  inspectorOpen,
  onToggleSource,
  onToggleInspector,
  readOnly = false,
  readOnlyReason = '',
  online = true,
  leaseHolder = null,
  onReturn,
}: TopBarProps) {
  const persisted = Boolean(document);
  const recognitionActive = recognitionState === 'starting' || recognitionState === 'running';
  const canRetryRecognition = Boolean(document?.game_prior)
    && recognitionRetryAllowed
    && Boolean(document?.source.original_url)
    && document?.status !== 'confirmed'
    && recognitionMode !== 'unavailable'
    && recognitionState === 'failed';
  const statusLabel = document ? {
    draft: '草稿',
    needs_review: '待人工核对',
    validated: '已校验',
    confirmed: '已提交',
  }[document.status] : '尚未选择比赛';
  const teamA = document?.game_prior?.team_a.name
    || document?.teams.find((team) => team.side === 'A')?.name
    || 'A队';
  const teamB = document?.game_prior?.team_b.name
    || document?.teams.find((team) => team.side === 'B')?.name
    || 'B队';
  const competition = document?.game_prior?.competition || document?.header.competition || '';

  return (
    <header className="topbar">
      <button className="return-to-media" type="button" onClick={onReturn}>
        <ArrowLeft size={16} />
        <span>返回比赛资料</span>
      </button>
      <div className="brand-block">
        <ScoresheetLogo className="scoresheet-logo brand-logo" title="ScoresheetReader 记录表" />
        <div>
          <strong>ScoresheetReader</strong>
          <span>语义记录表工作台</span>
        </div>
      </div>

      <div className="document-state">
        <span className={`state-dot ${document?.status ?? 'empty'}`} />
        <div>
          <strong>{document ? `${teamA} — ${teamB}` : '尚未选择比赛'}</strong>
          <span>{document ? `${competition || '未填写竞赛名称'} · ${statusLabel}` : '请选择比赛并上传记录表照片'}</span>
        </div>
      </div>

      <div className="topbar-actions">
        <div className="topbar-action-group panel-controls" aria-label="面板显示">
          <button className={sourceOpen ? 'icon-button is-active' : 'icon-button'} onClick={onToggleSource} title={sourceOpen ? '收起原图面板' : '展开原图面板'} aria-label={sourceOpen ? '收起原图面板' : '展开原图面板'}>
            {sourceOpen ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
          </button>
          <button className={inspectorOpen ? 'icon-button is-active' : 'icon-button'} onClick={onToggleInspector} title={inspectorOpen ? '收起编辑面板' : '展开编辑面板'} aria-label={inspectorOpen ? '收起编辑面板' : '展开编辑面板'}>
            {inspectorOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
          </button>
        </div>
        <div className="topbar-status-cluster">
          <span className="offline-badge">
            <ShieldCheck size={14} />
            {!online
              ? '网络已断开 · 未保存输入已保留'
              : readOnly
                ? readOnlyReason || `${leaseHolder?.username ?? '另一管理员'}正在通过${leaseHolder?.surface === 'MINIAPP' ? '小程序' : '网页'}编辑 · 实时只读`
                : `网页可编辑 · ${recognitionMode === 'unavailable' ? 'Qwen 未配置' : `${recognitionMode} 自动识别`}`}
          </span>
          {document ? <span className={`save-indicator ${saveState}`}><Save size={14} /> {saveLabels[saveState]}</span> : null}
        </div>
        <div className="topbar-action-group">
          <button className="topbar-button" onClick={onChooseGame}>
            <ListFilter size={15} /> 选择比赛
          </button>
        </div>
        {recognitionState === 'failed' ? (
          <div className="topbar-action-group recognition-workflow">
            <button
              className="topbar-button recognition-button"
              onClick={() => void onRecognize()}
              disabled={!canRetryRecognition || readOnly}
            >
              <ScanLine size={15} /> 重试识别
            </button>
          </div>
        ) : recognitionActive ? (
          <span className="recognition-running-label"><ScanLine size={15} className="pulse-icon" />识别中…</span>
        ) : null}
        <div className="topbar-action-group">
          <button className="icon-button" onClick={onUndo} disabled={!document || readOnly || !canUndo} title="撤销 Ctrl+Z" aria-label="撤销">
            <RotateCcw size={16} />
          </button>
          <button className="icon-button" onClick={onRedo} disabled={!document || readOnly || !canRedo} title="重做 Ctrl+Shift+Z" aria-label="重做">
            <RotateCw size={16} />
          </button>
        </div>
        <div className="topbar-action-group primary-workflow">
          <button
            className="topbar-button"
            onClick={() => void onSave()}
            disabled={!document || readOnly || saveState === 'saving' || saveState === 'saved'}
            title="立即保存当前草稿（Ctrl+S）"
          >
            <Save size={15} /> 保存草稿
          </button>
          <button className="topbar-button" onClick={() => void onValidate()} disabled={!document || readOnly}>
            <CheckCircle2 size={15} /> 校验
            {validation?.issues.length ? <b className="issue-count">{validation.issues.length}</b> : null}
          </button>
          {persisted ? (
            <a className="topbar-button" href={`/api/v1/scoresheets/${document!.id}/exports/pdf`} target="_blank" rel="noreferrer">
              <Download size={15} /> 导出 PDF
            </a>
          ) : (
            <button className="topbar-button" disabled title="请先选择比赛并上传记录表照片">
              <Download size={15} /> 导出 PDF
            </button>
          )}
          <button className="confirm-button" onClick={() => void onConfirm()} disabled={!document || !online || (!publicationRecoveryAllowed && (readOnly || document.status === 'confirmed'))}>
            <ShieldCheck size={15} /> {publicationRecoveryAllowed ? '核对原提交' : document?.status === 'confirmed' ? '已提交' : '提交记录表'}
          </button>
        </div>
      </div>
    </header>
  );
}

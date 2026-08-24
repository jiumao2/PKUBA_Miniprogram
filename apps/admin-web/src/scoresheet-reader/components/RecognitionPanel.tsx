import { AlertTriangle, Check, ChevronRight, LocateFixed, ScanLine, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { describeRecognitionProblem } from '../lib/fieldPaths';
import type { RecognitionDiff, RecognitionIssue, RecognitionRun, ScoresheetDocument } from '../types';

interface RecognitionPanelProps {
  run: RecognitionRun | null;
  diff: RecognitionDiff | null;
  state: 'idle' | 'starting' | 'running' | 'diff' | 'applied' | 'failed';
  document: ScoresheetDocument;
  problemPaths: string[];
  issues?: RecognitionIssue[];
  tablePersonnel?: string[];
  onApply: (regions: string[]) => Promise<void>;
  onDismissDiff: () => void;
  onLocateProblem: (path: string) => void;
  onResolveProblem: (path: string, code?: string) => void;
}

const STATUS_LABELS: Record<RecognitionRun['status'], string> = {
  pending: '任务已排队',
  connecting: '正在连接模型',
  thinking: '正在分析整张记录表',
  structuring: '正在生成结构化结果',
  validating: '正在校验并映射到编辑器',
  succeeded: '识别完成',
  failed: '识别失败',
  superseded: '已被新上传替代',
  interrupted: '识别被服务重启中断',
};

export function RecognitionPanel({
  run,
  diff,
  state,
  document,
  problemPaths,
  issues = [],
  tablePersonnel = [],
  onApply,
  onDismissDiff,
  onLocateProblem,
  onResolveProblem,
}: RecognitionPanelProps) {
  const changedRegions = useMemo(() => diff?.regions.filter((region) => region.changed) ?? [], [diff]);
  const [selected, setSelected] = useState<string[]>([]);

  useEffect(() => {
    setSelected(changedRegions.map((region) => region.region));
  }, [diff?.run_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const detailedPaths = new Set(issues.map((issue) => issue.path));
  const reviewItems = [
    ...issues.map((issue) => ({
      key: `${issue.code}:${issue.path}:${issue.message}`,
      code: issue.code,
      path: issue.path,
      message: issue.message,
    })),
    ...problemPaths
      .filter((path) => !detailedPaths.has(path))
      .map((path) => ({
        key: path,
        code: undefined,
        path,
        message: describeRecognitionProblem(path, document),
      })),
  ];
  if (!run && state === 'idle' && reviewItems.length === 0) return null;
  const active = state === 'starting' || state === 'running';
  return (
    <section className={`recognition-panel ${state}`} aria-label="大模型识别结果">
      <div className="section-title-row">
        <div>
          <span className="pane-kicker">整图识别</span>
          <h3>{active ? '正在读取记录表' : diff ? '选择要应用的区域' : state === 'failed' ? '识别未完成' : '识别结果已载入'}</h3>
        </div>
        <ScanLine size={19} className={active ? 'pulse-icon' : undefined} />
      </div>
      {active ? (
        <p className="section-note recognition-progress" aria-live="polite">
          <span className="recognition-progress-dot" />
          {run ? STATUS_LABELS[run.status] : '正在创建识别任务'}。期间可以继续查看当前草稿，但请等待识别完成后再编辑。
        </p>
      ) : null}
      {run ? (
        <div className="recognition-meta">
          <span>{run.model}</span>
          <span title={run.prompt_version}>提示词 {run.prompt_version.split('-').at(-1)}</span>
          <span>{run.cached ? '缓存命中' : '新请求'}</span>
          <span>输入 {run.usage.input_tokens}</span>
          <span>输出 {run.usage.output_tokens}</span>
          <span>图片 {run.usage.image_tokens}</span>
          {run.usage.reasoning_tokens > 0 ? <span>思考 {run.usage.reasoning_tokens}</span> : null}
          <strong>总计 {run.usage.total_tokens} tokens</strong>
        </div>
      ) : null}
      {run?.recognition_notes ? (
        <div className="recognition-note"><AlertTriangle size={15} /><span>{run.recognition_notes}</span></div>
      ) : null}
      {!active && state !== 'idle' && tablePersonnel.length > 0 ? (
        <div className="recognition-personnel" aria-label="识别到的记录台人员">
          <span>记录台人员 · 不分岗位</span>
          <div>{tablePersonnel.map((name) => <b key={name}>{name}</b>)}</div>
        </div>
      ) : null}
      {reviewItems.length > 0 ? (
        <div className="recognition-problems" aria-label="待人工核对的识别字段">
          <div className="recognition-problems-heading">
            <span>待人工核对</span>
            <strong>{reviewItems.length} 项</strong>
          </div>
          <p>逐项定位并确认；编辑高亮不会进入SVG或PDF导出。</p>
          <ol>
            {reviewItems.map((item) => (
              <li key={item.key}>
                <span title={item.path}>{item.message}</span>
                <div>
                  <button
                    className="problem-locate"
                    type="button"
                    aria-label={`定位：${item.message}`}
                    onClick={() => onLocateProblem(item.path)}
                  >
                    <LocateFixed size={12} />定位
                  </button>
                  <button
                    className="problem-resolve"
                    type="button"
                    aria-label={`已核对：${item.message}`}
                    onClick={() => item.code
                      ? onResolveProblem(item.path, item.code)
                      : onResolveProblem(item.path)}
                  >
                    <Check size={12} />已核对
                  </button>
                </div>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
      {diff ? (
        <>
          <div className="recognition-diff-list">
            {changedRegions.map((region) => {
              const checked = selected.includes(region.region);
              return (
                <label key={region.region} className={checked ? 'is-selected' : ''}>
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(event) => setSelected((current) => event.target.checked
                      ? [...current, region.region]
                      : current.filter((value) => value !== region.region))}
                  />
                  <span><Check size={13} />{region.label}</span>
                  <ChevronRight size={14} />
                </label>
              );
            })}
            {changedRegions.length === 0 ? <p className="section-note">新结果与当前草稿一致，没有需要合并的区域。</p> : null}
          </div>
          <div className="recognition-actions">
            <button className="secondary-action" onClick={onDismissDiff}><X size={14} />暂不应用</button>
            <button className="primary-action" disabled={selected.length === 0} onClick={() => void onApply(selected)}><Check size={14} />应用所选 {selected.length} 个区域</button>
          </div>
        </>
      ) : null}
    </section>
  );
}

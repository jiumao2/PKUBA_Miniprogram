import { useState } from 'react';
import type { ScoresheetContextPlayerMapping, ScoresheetGameContextReview } from '@pkuba/scoresheet-domain';

export function GameContextReview({ review, readOnly, busy, onConfirm }: {
  review: ScoresheetGameContextReview;
  readOnly: boolean;
  busy: boolean;
  onConfirm: (mappings: ScoresheetContextPlayerMapping[]) => Promise<void>;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [mappings, setMappings] = useState<Record<string, string>>({});
  return <section className="game-context-review" aria-label="比赛信息复核">
    <h3>核对比赛信息变化</h3>
    <p>原图和人工编辑均已保留。请对照原图核对下列差异，确认后重新校验，不会重新识别。</p>
    <dl>{review.differences.map((difference) => <div key={difference.field}>
      <dt>{difference.label}</dt>
      <dd><span>原先：{difference.before}</span><strong>当前：{difference.after}</strong></dd>
    </div>)}</dl>
    {review.player_conflicts.map((player) => {
      const key = `${player.side}:${player.row}`;
      return <label key={key} className="game-context-player">
        <span>{player.side} 队第 {player.row} 行 · {player.name}</span>
        <small>球队或球员身份已改变，请明确选择这行数据的归属。未选择仍不能发布。</small>
        <select disabled={readOnly || busy} value={mappings[key] ?? ''}
          onChange={(event) => setMappings((values) => ({ ...values, [key]: event.target.value }))}>
          <option value="">暂不确定，保留待核对</option>
          {player.choices.map((choice) => <option value={choice.id} key={choice.id}>{choice.label ?? choice.name}</option>)}
        </select>
      </label>;
    })}
    {!readOnly && <>
      <label className="game-context-consent"><input type="checkbox" checked={confirmed} disabled={busy}
        onChange={(event) => setConfirmed(event.target.checked)} />我已对照原图核对当前比赛信息及所选球员归属</label>
      <button disabled={!confirmed || busy} className="primary-action" onClick={() => void onConfirm(
        review.player_conflicts.flatMap((player) => mappings[`${player.side}:${player.row}`]
          ? [{ side: player.side, row: player.row, player_id: mappings[`${player.side}:${player.row}`] }] : []),
      )}>{busy ? '正在复核…' : '保留编辑并确认复核'}</button>
    </>}
  </section>;
}

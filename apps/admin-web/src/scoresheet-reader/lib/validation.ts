import type {
  FoulEntry,
  ScoresheetDocument,
  TeamSide,
  ValidationIssue,
  ValidationReport,
} from '../types';
import { teamBySide } from '../types';
import { describeRecognitionProblem } from './fieldPaths';
import { isValidJerseyNumber } from './jersey';
import {
  foulEditorOptions,
  ruleProfileAllowsFoulMarking,
  ruleProfileLabel,
} from './ruleProfiles';

const issue = (
  code: string,
  severity: ValidationIssue['severity'],
  paths: string[],
  message: string,
  observed: unknown = null,
  expected: unknown = null,
): ValidationIssue => ({ code, severity, paths, message, observed, expected });

const hasSlotGap = (entries: { slot: number }[]) => {
  const slots = entries.map((entry) => entry.slot).sort((a, b) => a - b);
  return slots.length > 0 && slots.some((slot, index) => slot !== index + 1);
};

const foulSuffix = (entry: FoulEntry) => (
  entry.free_throws != null ? String(entry.free_throws) : entry.cancelled ? 'c' : ''
) as '' | '1' | '2' | '3' | 'c';

export function validateLocal(document: ScoresheetDocument): ValidationReport {
  const issues: ValidationIssue[] = [];
  const ruleProfile = document.rules_profile ?? 'fiba_2024';
  const countedPlayerFoulCodes = new Set(
    foulEditorOptions(ruleProfile, 'player').map((option) => option.code),
  );
  document.recognition?.problem_paths.forEach((path) => {
    issues.push(issue(
      'RECOGNITION_REVIEW_REQUIRED',
      'warning',
      [path],
      describeRecognitionProblem(path, document),
    ));
  });
  document.recognition?.issues?.forEach((recognitionIssue) => {
    issues.push(issue(
      recognitionIssue.code,
      'warning',
      [recognitionIssue.path],
      recognitionIssue.message,
      recognitionIssue.observed,
      recognitionIssue.expected,
    ));
  });
  if (document.recognition?.notes.trim()) {
    issues.push(issue(
      'RECOGNITION_NOTES',
      'warning',
      ['/recognition/notes'],
      `模型备注：${document.recognition.notes.trim()}`,
    ));
  }
  (['A', 'B'] as TeamSide[]).forEach((side, teamIndex) => {
    const team = teamBySide(document, side);
    if (!team.name.trim()) issues.push(issue('MISSING_TEAM_NAME', 'error', [`/teams/${teamIndex}/name`], `${side} 队名称不能为空。`));
    const roster = team.players.filter((player) => player.jersey_number);
    if (roster.length < 5) issues.push(issue('MISSING_ROSTER', 'error', [`/teams/${teamIndex}/players`], `${side} 队至少需要 5 名有号码的队员。`, roster.length, '>= 5'));
    const counts = new Map<string, number>();
    team.players.forEach((player) => {
      if (!isValidJerseyNumber(player.jersey_number)) {
        issues.push(issue(
          'INVALID_JERSEY',
          'error',
          [`/teams/${teamIndex}/players/${player.row - 1}/jersey_number`],
          `${side} 队第 ${player.row} 行号码必须为 0、00 或 1–99。`,
          player.jersey_number,
          '0, 00, or 1-99',
        ));
      }
      if (player.jersey_number && !player.name.trim()) issues.push(issue('MISSING_PLAYER_NAME', 'warning', [`/teams/${teamIndex}/players/${player.row - 1}/name`], `${side} 队 ${player.jersey_number} 号尚未填写姓名。`));
      if (player.jersey_number) counts.set(player.jersey_number, (counts.get(player.jersey_number) ?? 0) + 1);
      const counted = player.fouls.filter((foul) => countedPlayerFoulCodes.has(foul.code)).length;
      if (counted > 5) issues.push(issue('FOUL_LIMIT_EXCEEDED', 'error', [`/teams/${teamIndex}/players/${player.row - 1}/fouls`], `${side} 队 ${player.jersey_number || player.name} 的计数犯规超过 5 次。`, counted, '<= 5'));
      if (hasSlotGap(player.fouls)) issues.push(issue('FOUL_SLOT_GAP', 'error', [`/teams/${teamIndex}/players/${player.row - 1}/fouls`], `${side} 队 ${player.jersey_number || player.name} 的犯规格必须从第 1 格连续填写。`));
      const postMarkers = player.post_foul_markers ?? [];
      if (hasSlotGap(postMarkers)) issues.push(issue('POST_FOUL_SLOT_GAP', 'error', [`/teams/${teamIndex}/players/${player.row - 1}/post_foul_markers`], '第五格后的附加标记必须连续填写。'));
      if (postMarkers.length && !player.fouls.some((foul) => foul.slot === 5)) issues.push(issue('POST_FOUL_WITHOUT_LAST_CELL', 'error', [`/teams/${teamIndex}/players/${player.row - 1}/post_foul_markers`], '只有第 5 个正式犯规格已填写后，才可使用其后的假想列。'));
      if (player.fouls.some((foul) => !ruleProfileAllowsFoulMarking(
        ruleProfile,
        foul.code,
        foul.mark_style ?? 'plain',
        'player',
        foulSuffix(foul),
      ))) issues.push(issue('FOUL_MARKING_NOT_IN_RULE_PROFILE', 'error', [`/teams/${teamIndex}/players/${player.row - 1}/fouls`], `该犯规写法属于其他规则版本，不能用于当前 ${ruleProfileLabel(ruleProfile)} 文档。`));
      if (postMarkers.some((foul) => !ruleProfileAllowsFoulMarking(
        ruleProfile,
        foul.code,
        foul.mark_style ?? 'plain',
        'post_foul',
        foulSuffix(foul),
      ))) issues.push(issue('FOUL_MARKING_NOT_IN_RULE_PROFILE', 'error', [`/teams/${teamIndex}/players/${player.row - 1}/post_foul_markers`], '该犯规写法不能用于队员正式犯规格后的附加列。'));
    });
    const duplicates = [...counts].filter(([, count]) => count > 1).map(([number]) => number);
    if (duplicates.length) issues.push(issue('DUPLICATE_JERSEY', 'error', [`/teams/${teamIndex}/players`], `${side} 队存在重复号码：${duplicates.join('、')}。`, duplicates));
    const starters = team.players.filter((player) => player.participation === 'starter').length;
    if (starters !== 5) issues.push(issue('STARTER_COUNT_MISMATCH', 'warning', [`/teams/${teamIndex}/players`], `${side} 队应标记 5 名首发队员。`, starters, 5));
    if (roster.length && !team.players.some((player) => player.captain)) issues.push(issue('MISSING_CAPTAIN', 'warning', [`/teams/${teamIndex}/players`], `${side} 队尚未标记队长。`));
    if (hasSlotGap(team.coach_fouls ?? [])) issues.push(issue('COACH_FOUL_SLOT_GAP', 'error', [`/teams/${teamIndex}/coach_fouls`], `${side} 队教练员的 3 个正式犯规格必须从第 1 格连续填写。`));
    const coachPost = team.coach_post_foul_markers ?? [];
    if (hasSlotGap(coachPost)) issues.push(issue('COACH_POST_FOUL_SLOT_GAP', 'error', [`/teams/${teamIndex}/coach_post_foul_markers`], '教练员第 3 格后的附加标记必须连续填写。'));
    if (coachPost.length && !team.coach_fouls.some((foul) => foul.slot === 3)) issues.push(issue('COACH_POST_FOUL_WITHOUT_LAST_CELL', 'error', [`/teams/${teamIndex}/coach_post_foul_markers`], '只有教练员第 3 个正式犯规格已填写后，才可使用其后的附加列。'));
    if (team.coach_fouls.some((foul) => !ruleProfileAllowsFoulMarking(ruleProfile, foul.code, foul.mark_style ?? 'plain', 'head_coach', foulSuffix(foul)))) issues.push(issue('FOUL_MARKING_NOT_IN_RULE_PROFILE', 'error', [`/teams/${teamIndex}/coach_fouls`], '该犯规写法不能用于主教练员犯规格。'));
    if (coachPost.some((foul) => !ruleProfileAllowsFoulMarking(ruleProfile, foul.code, foul.mark_style ?? 'plain', 'post_foul', foulSuffix(foul)))) issues.push(issue('FOUL_MARKING_NOT_IN_RULE_PROFILE', 'error', [`/teams/${teamIndex}/coach_post_foul_markers`], '该犯规写法不能用于教练员正式格后的附加列。'));
    const assistantFouls = [...(team.assistant_coach_fouls ?? [])].sort((left, right) => left.slot - right.slot);
    if (hasSlotGap(assistantFouls)) issues.push(issue('ASSISTANT_COACH_FOUL_SLOT_GAP', 'error', [`/teams/${teamIndex}/assistant_coach_fouls`], '助理教练员的 3 个犯规格必须从第 1 格连续填写。'));
    const assistantPost = team.assistant_coach_post_foul_markers ?? [];
    if (hasSlotGap(assistantPost)) issues.push(issue('ASSISTANT_COACH_POST_FOUL_SLOT_GAP', 'error', [`/teams/${teamIndex}/assistant_coach_post_foul_markers`], '助理教练员第 3 格后的附加标记必须连续填写。'));
    if (assistantPost.length && !assistantFouls.some((foul) => foul.slot === 3)) issues.push(issue('ASSISTANT_COACH_POST_FOUL_WITHOUT_LAST_CELL', 'error', [`/teams/${teamIndex}/assistant_coach_post_foul_markers`], '只有助理教练员第 3 个正式犯规格已填写后，才可使用其后的附加列。'));
    if (assistantFouls.some((foul) => !ruleProfileAllowsFoulMarking(ruleProfile, foul.code, foul.mark_style ?? 'plain', 'assistant_coach', foulSuffix(foul)))) issues.push(issue('FOUL_MARKING_NOT_IN_RULE_PROFILE', 'error', [`/teams/${teamIndex}/assistant_coach_fouls`], '该犯规写法不能用于助理教练员犯规格。'));
    if (assistantPost.some((foul) => !ruleProfileAllowsFoulMarking(ruleProfile, foul.code, foul.mark_style ?? 'plain', 'post_foul', foulSuffix(foul)))) issues.push(issue('FOUL_MARKING_NOT_IN_RULE_PROFILE', 'error', [`/teams/${teamIndex}/assistant_coach_post_foul_markers`], '该犯规写法不能用于助理教练员正式格后的附加列。'));

  });

  if (!document.header.competition.trim()) issues.push(issue('MISSING_COMPETITION', 'warning', ['/header/competition'], '竞赛名称尚未填写。'));
  if (!document.header.date.trim()) issues.push(issue('MISSING_DATE', 'warning', ['/header/date'], '日期尚未填写。'));
  if (!document.header.venue.trim()) issues.push(issue('MISSING_VENUE', 'warning', ['/header/venue'], '地点尚未填写。'));
  if (!document.header.scheduled_time.trim()) issues.push(issue('MISSING_SCHEDULED_TIME', 'warning', ['/header/scheduled_time'], '计划时间尚未填写。'));
  if (!document.score_events.length) issues.push(issue('MISSING_SCORE_EVENTS', 'error', ['/score_events'], '尚未录入任何逐次得分事件。'));
  if (!document.final_score.ended_at.trim()) issues.push(issue('MISSING_END_TIME', 'warning', ['/final_score/ended_at'], '比赛结束时间尚未填写。'));
  const orderedEvents = document.score_events
    .map((event, index) => ({ event, index }))
    .sort((left, right) => left.event.sequence - right.event.sequence);
  const observedSequences = orderedEvents.map(({ event }) => event.sequence);
  const expectedSequences = orderedEvents.map((_, index) => index + 1);
  if (observedSequences.some((sequence, index) => sequence !== expectedSequences[index])) {
    issues.push(issue('SCORE_EVENT_SEQUENCE_GAP', 'error', ['/score_events'], '逐次得分事件序号必须从 1 开始连续递增。', observedSequences, expectedSequences));
  }
  let previousPeriod = 0;
  orderedEvents.forEach(({ event, index }) => {
    if (event.period < previousPeriod) {
      issues.push(issue(
        'SCORE_PERIOD_ORDER',
        'error',
        [`/score_events/${index}/period`, '/score_events'],
        '逐次得分节次发生倒退；事件必须按第 1 节至决胜期顺序排列。',
        event.period,
        `>= ${previousPeriod}`,
      ));
    }
    previousPeriod = event.period;
  });
  const periodTotals = new Map<number, Record<TeamSide, number>>();
  const computedFinal: Record<TeamSide, number> = { A: 0, B: 0 };
  (['A', 'B'] as TeamSide[]).forEach((side) => {
    const team = teamBySide(document, side);
    const roster = new Set(team.players.map((player) => player.jersey_number).filter(Boolean));
    let previous = 0;
    document.score_events
      .filter((event) => event.team === side)
      .sort((a, b) => a.sequence - b.sequence)
      .forEach((event) => {
        const eventIndex = document.score_events.indexOf(event);
        const delta = event.cumulative_score - previous;
        const validPoints = event.points === 1 || event.points === 2 || event.points === 3;
        if (event.points == null) {
          issues.push(issue('UNRESOLVED_SCORE_POINTS', 'warning', [`/score_events/${eventIndex}/points`], `${side} 队累计 ${event.cumulative_score} 分的本次得分仍待确定。`, null, [1, 2, 3]));
        } else if (!validPoints) {
          issues.push(issue('INVALID_SCORE_POINTS', 'error', [`/score_events/${eventIndex}/points`], '每次得分只能是1、2或3分。', event.points, [1, 2, 3]));
        }
        if (![1, 2, 3].includes(delta) || (event.points != null && delta !== event.points)) {
          issues.push(issue(
            'SCORE_SEQUENCE_GAP',
            'error',
            [`/score_events/${eventIndex}/cumulative_score`],
            `${side} 队本次累计分与上一项相差 ${delta} 分；单次得分必须为1、2或3分，并与填写分值一致。`,
            event.cumulative_score,
            event.points == null ? `${previous + 1}至${previous + 3}` : previous + event.points,
          ));
        }
        previous = event.cumulative_score;
        const totals = periodTotals.get(event.period) ?? { A: 0, B: 0 };
        if (validPoints) totals[side] += event.points!;
        else if (event.points == null && [1, 2, 3].includes(delta)) totals[side] += delta;
        periodTotals.set(event.period, totals);
        if (!event.scorer_jersey) {
          issues.push(issue('MISSING_SCORER', 'error', [`/score_events/${eventIndex}/scorer_jersey`], `${side} 队累计 ${event.cumulative_score} 分尚未填写得分号码。`));
        } else if (!roster.has(event.scorer_jersey)) {
          issues.push(issue('UNKNOWN_SCORER', 'error', [`/score_events/${eventIndex}/scorer_jersey`], `得分号码 ${event.scorer_jersey} 不在 ${side} 队名单中。`));
        }
        const markOk =
          (event.points === 1 && event.mark === 'filled_dot' && !event.scorer_circled) ||
          (event.points === 2 && event.mark === 'diagonal' && !event.scorer_circled) ||
          (event.points === 3 && event.mark === 'diagonal' && event.scorer_circled);
        if (validPoints && !markOk) issues.push(issue('SCORE_MARK_DELTA_MISMATCH', 'error', [`/score_events/${eventIndex}`], '得分分值与黑点、斜杠或三分圈标记不一致。'));
      });
    computedFinal[side] = previous;
  });

  const periodsToCheck = Array.from(new Set([
    1,
    2,
    3,
    4,
    ...periodTotals.keys(),
    ...document.stated_period_scores.map((score) => score.period),
  ])).sort((left, right) => left - right);
  const periodCounts = new Map<number, number>();
  document.stated_period_scores.forEach((score) => {
    periodCounts.set(score.period, (periodCounts.get(score.period) ?? 0) + 1);
  });
  const duplicatePeriods = [...periodCounts]
    .filter(([, count]) => count > 1)
    .map(([period]) => period)
    .sort((left, right) => left - right);
  if (duplicatePeriods.length) {
    issues.push(issue(
      'DUPLICATE_PERIOD_SCORE',
      'error',
      document.stated_period_scores
        .map((score, index) => duplicatePeriods.includes(score.period) ? `/stated_period_scores/${index}` : '')
        .filter(Boolean),
      '每个节次只能填写一行书面节比分。',
      duplicatePeriods,
      'unique periods',
    ));
  }
  periodsToCheck.forEach((period) => {
    const totals = periodTotals.get(period) ?? { A: 0, B: 0 };
    const stated = document.stated_period_scores.find((score) => score.period === period);
    if (!stated) issues.push(issue('MISSING_PERIOD_SCORE', 'error', [`/stated_period_scores/${period}`], `第 ${period} 节的书面节比分尚未填写，无法核对累计分。`));
    else if (stated.team_a !== totals.A || stated.team_b !== totals.B) issues.push(issue('PERIOD_SCORE_MISMATCH', 'error', ['/stated_period_scores', '/score_events'], `第 ${period} 节书面比分与逐次得分不一致。`, { A: stated.team_a, B: stated.team_b }, totals));
  });

  const observed = { A: document.final_score.team_a, B: document.final_score.team_b };
  if (observed.A !== computedFinal.A || observed.B !== computedFinal.B) issues.push(issue('FINAL_SCORE_MISMATCH', 'error', ['/final_score', '/score_events'], '书面最终比分与累计分最后结果不一致。', observed, computedFinal));
  const statedSum = document.stated_period_scores.reduce((sum, score) => ({ A: sum.A + score.team_a, B: sum.B + score.team_b }), { A: 0, B: 0 });
  if (observed.A !== statedSum.A || observed.B !== statedSum.B) issues.push(issue('PERIOD_SUM_MISMATCH', 'error', ['/final_score', '/stated_period_scores'], '各节比分之和与书面最终比分不一致。', observed, statedSum));
  const canonicalA = document.game_prior?.team_a.name ?? teamBySide(document, 'A').name;
  const canonicalB = document.game_prior?.team_b.name ?? teamBySide(document, 'B').name;
  const expectedWinner = observed.A > observed.B ? canonicalA : observed.B > observed.A ? canonicalB : '';
  if (!expectedWinner) {
    issues.push(issue(
      'TIED_FINAL_SCORE',
      'error',
      ['/final_score/team_a', '/final_score/team_b'],
      '篮球比赛终场不能为平分；请检查最终比分和可能遗漏的决胜期记录。',
      observed,
      '两队最终比分不同',
    ));
  } else if (document.final_score.winner_name !== expectedWinner) {
    issues.push(issue(
      'WINNER_MISMATCH',
      'error',
      ['/final_score/winner_name'],
      '胜队必须是最终比分更高一队的主数据标准名称。',
      document.final_score.winner_name,
      expectedWinner,
    ));
  }

  return {
    status: issues.some((entry) => entry.severity === 'error')
      ? 'invalid'
      : issues.some((entry) => entry.severity === 'warning')
        ? 'needs_review'
        : 'valid',
    issues,
    checked_at: new Date().toISOString(),
  };
}

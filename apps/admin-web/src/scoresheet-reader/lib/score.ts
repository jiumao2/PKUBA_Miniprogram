import type { GamePeriod, ScoreEvent, ScoresheetDocument, TeamSide } from '../types';

export function semanticMark(points: number | null): Pick<ScoreEvent, 'mark' | 'scorer_circled'> {
  if (points === 1) return { mark: 'filled_dot', scorer_circled: false };
  if (points === 2) return { mark: 'diagonal', scorer_circled: false };
  if (points === 3) return { mark: 'diagonal', scorer_circled: true };
  return { mark: null, scorer_circled: false };
}

export function periodCheckpoints(
  document: ScoresheetDocument,
  side: TeamSide,
): Array<{ period: GamePeriod; cumulative: number }> {
  const byPeriod = new Map<number, (typeof document.stated_period_scores)[number]>();
  document.stated_period_scores.forEach((score) => {
    if (!byPeriod.has(score.period)) byPeriod.set(score.period, score);
  });
  const periods: GamePeriod[] = [1, 2, 3, 4];
  if (byPeriod.has(5)) periods.push(5);
  let cumulative = 0;
  return periods.map((period) => {
    const score = byPeriod.get(period);
    if (score) cumulative += side === 'A' ? score.team_a : score.team_b;
    return { period, cumulative };
  });
}

function periodForScore(
  cumulativeScore: number,
  checkpoints: Array<{ period: GamePeriod; cumulative: number }>,
): GamePeriod {
  const covering = checkpoints.find((checkpoint) => cumulativeScore <= checkpoint.cumulative);
  if (covering) return covering.period;
  const last = checkpoints.at(-1);
  return last && last.cumulative > 0 ? last.period : 1;
}

export function deriveScoreEvents(document: ScoresheetDocument): ScoresheetDocument {
  const bySide = new Map<TeamSide, ScoreEvent[]>([
    ['A', document.score_events.filter((event) => event.team === 'A')],
    ['B', document.score_events.filter((event) => event.team === 'B')],
  ]);

  (['A', 'B'] as TeamSide[]).forEach((side) => {
    const events = bySide.get(side)!
      .sort((left, right) => left.cumulative_score - right.cumulative_score || left.sequence - right.sequence);
    const checkpoints = periodCheckpoints(document, side);
    let previous = 0;
    events.forEach((event) => {
      const delta = event.cumulative_score - previous;
      event.points = delta >= 1 ? delta : null;
      Object.assign(event, semanticMark(event.points));
      event.period = periodForScore(event.cumulative_score, checkpoints);
      event.ink_role = event.period === 1 || event.period === 3 ? 'q1_q3' : 'q2_q4_ot';
      event.boundary = 'none';
      previous = event.cumulative_score;
    });
    const byCumulative = new Map(events.map((event) => [event.cumulative_score, event]));
    checkpoints.forEach(({ cumulative }) => {
      if (cumulative > 0) {
        const event = byCumulative.get(cumulative);
        if (event) event.boundary = 'period_end';
      }
    });
  });

  const latest = (side: TeamSide) => bySide.get(side)!.at(-1);
  const latestA = latest('A');
  const latestB = latest('B');
  if (
    latestA
    && latestB
    && latestA.cumulative_score === document.final_score.team_a
    && latestB.cumulative_score === document.final_score.team_b
  ) {
    latestA.boundary = 'game_end';
    latestB.boundary = 'game_end';
  }

  document.score_events.sort((left, right) => (
    left.period - right.period
    || left.team.localeCompare(right.team)
    || left.cumulative_score - right.cumulative_score
    || left.sequence - right.sequence
  ));
  document.score_events.forEach((event, index) => { event.sequence = index + 1; });
  return document;
}

export function setScoreCell(
  document: ScoresheetDocument,
  side: TeamSide,
  cumulativeScore: number,
  scorerJersey: string,
): ScoreEvent {
  let event = document.score_events.find(
    (candidate) => candidate.team === side && candidate.cumulative_score === cumulativeScore,
  );
  if (!event) {
    event = {
      sequence: Math.max(0, ...document.score_events.map((candidate) => candidate.sequence)) + 1,
      team: side,
      period: 1,
      points: null,
      cumulative_score: cumulativeScore,
      scorer_jersey: scorerJersey,
      mark: null,
      scorer_circled: false,
      boundary: 'none',
      ink_role: 'neutral',
    };
    document.score_events.push(event);
  } else {
    event.scorer_jersey = scorerJersey;
  }
  deriveScoreEvents(document);
  return document.score_events.find(
    (candidate) => candidate.team === side && candidate.cumulative_score === cumulativeScore,
  )!;
}

export function removeScoreCell(
  document: ScoresheetDocument,
  side: TeamSide,
  cumulativeScore: number,
): ScoresheetDocument {
  document.score_events = document.score_events.filter(
    (event) => event.team !== side || event.cumulative_score !== cumulativeScore,
  );
  return deriveScoreEvents(document);
}

export function scoreTotalsByPeriod(
  document: ScoresheetDocument,
  side: TeamSide,
): Map<number, number> {
  const totals = new Map<number, number>();
  document.score_events
    .filter((event) => event.team === side)
    .forEach((event) => {
      if (event.points === 1 || event.points === 2 || event.points === 3) {
        totals.set(event.period, (totals.get(event.period) ?? 0) + event.points);
      }
    });
  return totals;
}

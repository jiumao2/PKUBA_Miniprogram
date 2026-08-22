import type { ScoreEvent, ScoresheetDocument, TeamSide } from '../types';

export type ScoreInsertPosition = 'before' | 'after' | 'end';

export interface NewScoreEvent {
  period: number;
  points: number;
  scorerJersey: string;
}

export function semanticMark(points: number | null): Pick<ScoreEvent, 'mark' | 'scorer_circled'> {
  if (points === 1) return { mark: 'filled_dot', scorer_circled: false };
  if (points === 2) return { mark: 'diagonal', scorer_circled: false };
  if (points === 3) return { mark: 'diagonal', scorer_circled: true };
  return { mark: null, scorer_circled: false };
}

export function recalculateTeamEvents(
  document: ScoresheetDocument,
  side: TeamSide,
): ScoresheetDocument {
  let cumulative = 0;
  const ordered = document.score_events
    .filter((event) => event.team === side)
    .sort((a, b) => a.sequence - b.sequence);
  for (const event of ordered) {
    if (event.points == null) {
      cumulative = event.cumulative_score;
    } else {
      cumulative += event.points;
      event.cumulative_score = cumulative;
    }
    Object.assign(event, semanticMark(event.points));
    event.ink_role = event.period === 1 || event.period === 3 ? 'q1_q3' : 'q2_q4_ot';
  }
  return document;
}

export function nextCumulative(document: ScoresheetDocument, side: TeamSide): number {
  return Math.max(
    0,
    ...document.score_events
      .filter((event) => event.team === side)
      .map((event) => event.cumulative_score),
  );
}

export function resequence(document: ScoresheetDocument): ScoresheetDocument {
  document.score_events
    .sort((a, b) => a.sequence - b.sequence)
    .forEach((event, index) => {
      event.sequence = index + 1;
    });
  return document;
}

export function scoreTotalsByPeriod(
  document: ScoresheetDocument,
  side: TeamSide,
): Map<number, number> {
  const totals = new Map<number, number>();
  let previous = 0;
  const events = document.score_events
    .filter((event) => event.team === side)
    .sort((left, right) => left.sequence - right.sequence);
  for (const event of events) {
    const delta = event.cumulative_score - previous;
    if (event.points === 1 || event.points === 2 || event.points === 3) {
      totals.set(event.period, (totals.get(event.period) ?? 0) + event.points);
    } else if (event.points == null && [1, 2, 3].includes(delta)) {
      totals.set(event.period, (totals.get(event.period) ?? 0) + delta);
    }
    previous = event.cumulative_score;
  }
  return totals;
}

export function insertScoreEvent(
  document: ScoresheetDocument,
  side: TeamSide,
  anchorSequence: number | null,
  position: ScoreInsertPosition,
  input: NewScoreEvent,
): ScoreEvent {
  const anchor = anchorSequence == null
    ? undefined
    : document.score_events.find((event) => event.sequence === anchorSequence);
  const maxSequence = Math.max(0, ...document.score_events.map((event) => event.sequence));
  const sequence = position === 'end' || !anchor
    ? maxSequence + 1
    : anchor.sequence + (position === 'before' ? -0.5 : 0.5);
  const points = Math.min(3, Math.max(1, Math.round(input.points)));
  const inserted: ScoreEvent = {
    sequence,
    team: side,
    period: input.period,
    points,
    cumulative_score: 0,
    scorer_jersey: input.scorerJersey,
    ...semanticMark(points),
    boundary: 'none',
    ink_role: input.period === 1 || input.period === 3 ? 'q1_q3' : 'q2_q4_ot',
  };
  if (
    anchor
    && position === 'after'
    && anchor.team === side
    && anchor.period === input.period
    && anchor.boundary === 'period_end'
  ) {
    anchor.boundary = 'none';
    inserted.boundary = 'period_end';
  }
  document.score_events.push(inserted);
  resequence(document);
  recalculateTeamEvents(document, side);
  return inserted;
}

export function removeScoreEvent(
  document: ScoresheetDocument,
  sequence: number,
): ScoresheetDocument {
  const removed = document.score_events.find((event) => event.sequence === sequence);
  if (!removed) return document;
  if (removed.boundary === 'period_end') {
    const replacement = document.score_events
      .filter(
        (event) => event.team === removed.team
          && event.period === removed.period
          && event.sequence < removed.sequence,
      )
      .sort((a, b) => b.sequence - a.sequence)[0];
    if (replacement) replacement.boundary = 'period_end';
  }
  document.score_events = document.score_events.filter((event) => event.sequence !== sequence);
  resequence(document);
  recalculateTeamEvents(document, removed.team);
  return document;
}

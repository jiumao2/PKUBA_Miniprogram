import { describe, expect, it } from 'vitest';
import { makeDocument } from '../test/fixtures';
import {
  insertScoreEvent,
  recalculateTeamEvents,
  removeScoreEvent,
  resequence,
  scoreTotalsByPeriod,
  semanticMark,
} from './score';

describe('semantic scoring operations', () => {
  it('maps point values to standardized paper marks', () => {
    expect(semanticMark(1)).toEqual({ mark: 'filled_dot', scorer_circled: false });
    expect(semanticMark(2)).toEqual({ mark: 'diagonal', scorer_circled: false });
    expect(semanticMark(3)).toEqual({ mark: 'diagonal', scorer_circled: true });
    expect(semanticMark(null)).toEqual({ mark: null, scorer_circled: false });
  });

  it('recalculates every later cumulative score for the edited team only', () => {
    const document = makeDocument();
    document.score_events[0].points = 3;
    document.score_events[0].mark = 'filled_dot';

    recalculateTeamEvents(document, 'A');

    expect(document.score_events.filter((event) => event.team === 'A').map((event) => event.cumulative_score)).toEqual([3, 5, 8]);
    expect(document.score_events.filter((event) => event.team === 'B').map((event) => event.cumulative_score)).toEqual([2, 5]);
    expect(document.score_events[0]).toMatchObject({ mark: 'diagonal', scorer_circled: true, ink_role: 'q1_q3' });
  });

  it('preserves an unresolved row as an anchor and recalculates after it is fixed', () => {
    const document = makeDocument();
    const middleA = document.score_events.find((event) => event.team === 'A' && event.cumulative_score === 3)!;
    middleA.points = null;
    middleA.mark = null;

    recalculateTeamEvents(document, 'A');
    expect(document.score_events.filter((event) => event.team === 'A').map((event) => event.cumulative_score))
      .toEqual([1, 3, 6]);
    expect(middleA).toMatchObject({ mark: null, scorer_circled: false });

    middleA.points = 1;
    recalculateTeamEvents(document, 'A');
    expect(document.score_events.filter((event) => event.team === 'A').map((event) => event.cumulative_score))
      .toEqual([1, 2, 5]);
  });

  it('rebuilds stable sequence numbers after deletion', () => {
    const document = makeDocument();
    document.score_events.splice(1, 1);
    resequence(document);

    expect(document.score_events.map((event) => event.sequence)).toEqual([1, 2, 3, 4]);
  });

  it('inserts an event at the requested chronological position and recalculates that team', () => {
    const document = makeDocument();
    const anchor = document.score_events.find((event) => event.team === 'A' && event.cumulative_score === 3)!;

    const inserted = insertScoreEvent(document, 'A', anchor.sequence, 'before', {
      period: 2,
      points: 1,
      scorerJersey: '8',
    });

    expect(inserted).toMatchObject({ cumulative_score: 2, points: 1, scorer_jersey: '8' });
    expect(document.score_events.filter((event) => event.team === 'A').map((event) => event.cumulative_score))
      .toEqual([1, 2, 4, 7]);
    expect(document.score_events.filter((event) => event.team === 'B').map((event) => event.cumulative_score))
      .toEqual([2, 5]);
    expect(document.score_events.map((event) => event.sequence)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it('moves a period-end boundary to the preceding event when its event is deleted', () => {
    const document = makeDocument();
    const lastA = document.score_events.find((event) => event.team === 'A' && event.cumulative_score === 6)!;
    lastA.boundary = 'period_end';

    removeScoreEvent(document, lastA.sequence);

    const remainingA = document.score_events.filter((event) => event.team === 'A');
    expect(remainingA.map((event) => event.cumulative_score)).toEqual([1, 3]);
    expect(remainingA[1].boundary).toBe('period_end');
  });

  it('keeps event-derived period totals separate from written period scores', () => {
    const document = makeDocument();
    document.stated_period_scores[1].team_a = 99;

    expect(Object.fromEntries(scoreTotalsByPeriod(document, 'A'))).toEqual({ 1: 1, 2: 5 });
    expect(document.stated_period_scores[1].team_a).toBe(99);
  });
});

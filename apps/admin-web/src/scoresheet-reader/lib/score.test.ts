import { describe, expect, it } from 'vitest';
import { makeDocument } from '../test/fixtures';
import {
  deriveScoreEvents,
  periodCheckpoints,
  removeScoreCell,
  scoreTotalsByPeriod,
  semanticMark,
  setScoreCell,
} from './score';

describe('fixed cumulative-score cells', () => {
  it('maps only valid derived gaps to standardized paper marks', () => {
    expect(semanticMark(1)).toEqual({ mark: 'filled_dot', scorer_circled: false });
    expect(semanticMark(2)).toEqual({ mark: 'diagonal', scorer_circled: false });
    expect(semanticMark(3)).toEqual({ mark: 'diagonal', scorer_circled: true });
    expect(semanticMark(4)).toEqual({ mark: null, scorer_circled: false });
    expect(semanticMark(null)).toEqual({ mark: null, scorer_circled: false });
  });

  it('derives points and marks without moving any cumulative-score cell', () => {
    const document = makeDocument();
    const firstA = document.score_events.find(
      (event) => event.team === 'A' && event.cumulative_score === 1,
    )!;
    firstA.points = 3;
    firstA.mark = 'diagonal';
    firstA.scorer_circled = true;

    deriveScoreEvents(document);

    expect(document.score_events.filter((event) => event.team === 'A'))
      .toMatchObject([
        { cumulative_score: 1, points: 1, mark: 'filled_dot', scorer_circled: false },
        { cumulative_score: 3, points: 2, mark: 'diagonal', scorer_circled: false },
        { cumulative_score: 6, points: 3, mark: 'diagonal', scorer_circled: true },
      ]);
  });

  it('fills a blank fixed cell without shifting later scores', () => {
    const document = makeDocument();

    setScoreCell(document, 'A', 2, '8');

    expect(document.score_events.filter((event) => event.team === 'A'))
      .toMatchObject([
        { cumulative_score: 1, points: 1 },
        { cumulative_score: 2, points: 1, scorer_jersey: '8' },
        { cumulative_score: 3, points: 1 },
        { cumulative_score: 6, points: 3 },
      ]);
  });

  it('deletes only the selected cell and preserves an invalid derived gap for review', () => {
    const document = makeDocument();

    removeScoreCell(document, 'A', 3);

    expect(document.score_events.filter((event) => event.team === 'A'))
      .toMatchObject([
        { cumulative_score: 1, points: 1 },
        { cumulative_score: 6, points: 5, mark: null, scorer_circled: false },
      ]);
    expect(Object.fromEntries(scoreTotalsByPeriod(document, 'A'))).toMatchObject({ 1: 1 });
  });

  it('accepts a newly derived one-to-three point gap without retaining old evidence state', () => {
    const document = makeDocument();

    removeScoreCell(document, 'A', 1);

    expect(document.score_events.filter((event) => event.team === 'A'))
      .toMatchObject([
        { cumulative_score: 3, points: 3, mark: 'diagonal', scorer_circled: true },
        { cumulative_score: 6, points: 3, mark: 'diagonal', scorer_circled: true },
      ]);
  });

  it('preserves an unreadable scorer as a distinct pending cell', () => {
    const document = makeDocument();
    const event = document.score_events.find(
      (candidate) => candidate.team === 'A' && candidate.cumulative_score === 3,
    )!;
    event.scorer_jersey = '';

    deriveScoreEvents(document);

    expect(document.score_events.find(
      (candidate) => candidate.team === 'A' && candidate.cumulative_score === 3,
    )).toMatchObject({ scorer_jersey: '', points: 2 });
  });

  it('derives period checkpoints and boundaries from written period scores', () => {
    const document = makeDocument();

    deriveScoreEvents(document);

    expect(periodCheckpoints(document, 'A')).toEqual([
      { period: 1, cumulative: 1 },
      { period: 2, cumulative: 6 },
      { period: 3, cumulative: 6 },
      { period: 4, cumulative: 6 },
    ]);
    expect(document.score_events.find(
      (event) => event.team === 'A' && event.cumulative_score === 1,
    )?.boundary).toBe('period_end');
    expect(document.score_events.find(
      (event) => event.team === 'A' && event.cumulative_score === 6,
    )?.boundary).toBe('game_end');
  });

  it('stores all physical overtimes as one combined period five', () => {
    const document = makeDocument();
    document.stated_period_scores.push({ period: 5, team_a: 2, team_b: 1 });
    document.score_events.push(
      { sequence: 6, team: 'A', period: 5, points: 1, cumulative_score: 7, scorer_jersey: '8', mark: 'filled_dot', scorer_circled: false, boundary: 'period_end', ink_role: 'q2_q4_ot' },
      { sequence: 7, team: 'A', period: 5, points: 1, cumulative_score: 8, scorer_jersey: '8', mark: 'filled_dot', scorer_circled: false, boundary: 'none', ink_role: 'q2_q4_ot' },
      { sequence: 8, team: 'B', period: 5, points: 1, cumulative_score: 6, scorer_jersey: '9', mark: 'filled_dot', scorer_circled: false, boundary: 'none', ink_role: 'q2_q4_ot' },
    );
    document.final_score.team_a = 8;
    document.final_score.team_b = 6;

    deriveScoreEvents(document);

    expect(periodCheckpoints(document, 'A').at(-1)).toEqual({ period: 5, cumulative: 8 });
    expect(document.score_events.find(
      (event) => event.team === 'A' && event.cumulative_score === 7,
    )?.boundary).toBe('none');
    expect(document.score_events.find(
      (event) => event.team === 'A' && event.cumulative_score === 8,
    )?.boundary).toBe('game_end');
    expect(document.score_events
      .filter((event) => (
        (event.team === 'A' && event.cumulative_score >= 7)
        || (event.team === 'B' && event.cumulative_score >= 6)
      ))
      .map((event) => event.period)).toEqual([5, 5, 5]);
  });

  it('keeps derived period totals separate from written period scores', () => {
    const document = makeDocument();
    document.stated_period_scores[1].team_a = 99;

    deriveScoreEvents(document);

    expect(Object.fromEntries(scoreTotalsByPeriod(document, 'A'))).toEqual({ 1: 1, 2: 5 });
    expect(document.stated_period_scores[1].team_a).toBe(99);
  });
});

import { describe, expect, it } from 'vitest';
import { makeDocument } from '../test/fixtures';
import { isValidJerseyNumber } from './jersey';
import { validateLocal } from './validation';

const codes = (document: ReturnType<typeof makeDocument>) =>
  validateLocal(document).issues.map((issue) => issue.code);

describe('instant deterministic validation', () => {
  it.each(['0', '00', '1', '99'])('accepts legal jersey %s', (jersey) => {
    expect(isValidJerseyNumber(jersey)).toBe(true);
  });

  it.each(['01', '100', '-1', 'A7'])('rejects illegal jersey %s', (jersey) => {
    expect(isValidJerseyNumber(jersey)).toBe(false);
  });

  it('accepts the valid synthetic document', () => {
    expect(validateLocal(makeDocument())).toMatchObject({ status: 'valid', issues: [] });
  });

  it('reports duplicate and malformed roster numbers immediately', () => {
    const document = makeDocument();
    document.teams[0].players[0].jersey_number = '01';
    document.teams[0].players[1].jersey_number = document.teams[0].players[2].jersey_number;

    expect(codes(document)).toEqual(expect.arrayContaining(['INVALID_JERSEY', 'DUPLICATE_JERSEY']));
  });

  it('cross-checks marks, period totals, final score and winner', () => {
    const document = makeDocument();
    document.score_events[0].mark = 'diagonal';
    document.stated_period_scores[0].team_a = 2;
    document.final_score.team_a = 4;
    document.final_score.winner_name = document.teams[0].name;

    expect(codes(document)).toEqual(expect.arrayContaining([
      'SCORE_MARK_DELTA_MISMATCH',
      'PERIOD_SCORE_MISMATCH',
      'FINAL_SCORE_MISMATCH',
      'PERIOD_SUM_MISMATCH',
      'WINNER_MISMATCH',
    ]));
  });

  it('reports invalid imported points and keeps unresolved points reviewable', () => {
    const invalid = makeDocument();
    invalid.score_events[0].points = 4;
    invalid.score_events[0].mark = null;
    expect(codes(invalid)).toEqual(expect.arrayContaining([
      'INVALID_SCORE_POINTS',
    ]));

    const unresolved = makeDocument();
    unresolved.score_events[0].points = null;
    unresolved.score_events[0].mark = null;
    const report = validateLocal(unresolved);
    expect(report.issues).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'UNRESOLVED_SCORE_POINTS', severity: 'warning' }),
    ]));
    expect(report.status).toBe('needs_review');
  });

  it('rejects duplicate cells and derived gaps that cross a written checkpoint', () => {
    const duplicate = makeDocument();
    duplicate.score_events.push({ ...duplicate.score_events[0], sequence: 6 });
    expect(codes(duplicate)).toContain('DUPLICATE_SCORE_CELL');

    const gap = makeDocument();
    gap.score_events = gap.score_events.filter(
      (event) => event.team !== 'A' || event.cumulative_score !== 3,
    );
    const later = gap.score_events.find(
      (event) => event.team === 'A' && event.cumulative_score === 6,
    )!;
    later.points = 5;
    later.mark = null;
    later.scorer_circled = false;

    expect(codes(gap)).toEqual(expect.arrayContaining([
      'INVALID_SCORE_POINTS',
      'SCORE_SEQUENCE_GAP',
    ]));

    const boundary = makeDocument();
    boundary.score_events = boundary.score_events.filter(
      (event) => event.team !== 'A' || event.cumulative_score !== 1,
    );
    expect(codes(boundary)).toEqual(expect.arrayContaining([
      'SCORE_EVENT_CROSSES_PERIOD_BOUNDARY',
      'PERIOD_BOUNDARY_WITHOUT_EVENT',
    ]));
  });

  it('treats a written period with no running-score events as a mandatory mismatch', () => {
    const document = makeDocument();
    document.score_events = document.score_events
      .filter((event) => event.period !== 2)
      .map((event, index) => ({ ...event, sequence: index + 1 }));

    const mismatch = validateLocal(document).issues.find((entry) => entry.code === 'PERIOD_SCORE_MISMATCH');
    expect(mismatch).toMatchObject({
      severity: 'error',
      observed: { A: 5, B: 3 },
      expected: { A: 0, B: 0 },
    });
  });

  it('requires written period totals and chronological period order', () => {
    const document = makeDocument();
    document.stated_period_scores = document.stated_period_scores.filter((score) => score.period !== 2);
    document.score_events[3].period = 1;
    document.score_events.at(-1)!.sequence += 1;

    const report = validateLocal(document);
    expect(report.issues).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: 'MISSING_PERIOD_SCORE', severity: 'error' }),
      expect.objectContaining({ code: 'SCORE_PERIOD_ORDER', severity: 'error' }),
      expect.objectContaining({ code: 'SCORE_EVENT_SEQUENCE_GAP', severity: 'error' }),
    ]));
  });

  it('rejects duplicate written period rows', () => {
    const document = makeDocument();
    document.stated_period_scores.push({ ...document.stated_period_scores[0] });

    expect(validateLocal(document).issues).toEqual(expect.arrayContaining([
      expect.objectContaining({
        code: 'DUPLICATE_PERIOD_SCORE',
        severity: 'error',
        paths: ['/stated_period_scores/0', '/stated_period_scores/4'],
      }),
    ]));
  });

  it('requires first assistant coach fouls to start in the first cell', () => {
    const document = makeDocument();
    document.teams[0].assistant_coach_fouls = [
      { slot: 2, code: 'C', free_throws: null, cancelled: false, period: 3 },
    ];

    expect(codes(document)).toContain('ASSISTANT_COACH_FOUL_SLOT_GAP');
  });

  it('validates foul codes against player, coach and post-foul subjects', () => {
    const document = makeDocument();
    document.teams[0].players[0].fouls = [
      { slot: 1, code: 'C', free_throws: null, cancelled: false, period: 1 },
    ];
    document.teams[0].coach_fouls = [
      { slot: 1, code: 'P', free_throws: null, cancelled: false, period: 1 },
    ];
    document.teams[0].coach_post_foul_markers = [
      { slot: 1, code: 'P', free_throws: null, cancelled: false, period: 1 },
    ];

    expect(codes(document).filter((code) => code === 'FOUL_MARKING_NOT_IN_RULE_PROFILE')).toHaveLength(3);
  });

  it('requires a scorer and never derives team-foul boxes from personal fouls', () => {
    const document = makeDocument();
    document.score_events[0].scorer_jersey = '';
    document.teams[0].team_fouls[0].count = 4;

    expect(codes(document)).toContain('MISSING_SCORER');
    expect(codes(document)).not.toContain('UNKNOWN_SCORER');
    expect(codes(document)).not.toContain('TEAM_FOUL_MISMATCH');
  });

  it('requires a non-tied final and the canonical higher-scoring team name', () => {
    const tied = makeDocument();
    tied.final_score.team_b = tied.final_score.team_a;
    expect(codes(tied)).toContain('TIED_FINAL_SCORE');

    const wrongWinner = makeDocument();
    wrongWinner.game_prior = {
      game_id: 'game', competition: '测试杯', division: '男甲', date: '2026-08-19',
      scheduled_time: '14:00', venue: '球馆', source_hash: 'hash', locked_paths: [],
      team_a: { team_id: 'a', name: '甲队标准名', player_names: [] },
      team_b: { team_id: 'b', name: '乙队标准名', player_names: [] },
    };
    wrongWinner.final_score.winner_name = wrongWinner.teams[0].name;
    const winnerIssue = validateLocal(wrongWinner).issues.find((entry) => entry.code === 'WINNER_MISMATCH');
    expect(winnerIssue).toMatchObject({ expected: '甲队标准名' });
  });

  it('names every recognition uncertainty instead of repeating a generic warning', () => {
    const document = makeDocument();
    document.recognition = {
      run_id: 'run', notes: '', table_personnel: [],
      problem_paths: [
        '/teams/0/assistant_coach',
        '/score_events/B/cumulative/5/scorer_jersey',
      ],
      applied_at: '2026-08-19T00:00:00Z',
    };

    const messages = validateLocal(document).issues
      .filter((entry) => entry.code === 'RECOGNITION_REVIEW_REQUIRED')
      .map((entry) => entry.message);
    expect(messages).toEqual([
      'A 队助理教练员姓名未能可靠确定',
      'B 队累计 5 分的得分号码未能可靠确定',
    ]);
  });

  it('preserves detailed recognition warnings with their exact score location', () => {
    const document = makeDocument();
    document.recognition = {
      run_id: 'run', notes: '', table_personnel: [], problem_paths: [],
      issues: [{
        code: 'RUNNING_SCORE_MARK_MISSING',
        path: '/score_events/A/cumulative/3/has_score_mark',
        message: 'A 队累计 3 分没有识别到黑点或斜杠。',
        observed: false,
        expected: true,
      }],
      applied_at: '2026-08-20T00:00:00Z',
    };

    expect(validateLocal(document).issues).toEqual(expect.arrayContaining([
      expect.objectContaining({
        code: 'RUNNING_SCORE_MARK_MISSING',
        paths: ['/score_events/A/cumulative/3/has_score_mark'],
      }),
    ]));
  });

  it('reports missing roster, scores and end time while allowing all personnel to be empty', () => {
    const document = makeDocument();
    document.teams[0].players = [];
    document.score_events = [];
    document.stated_period_scores = [];
    document.final_score = { team_a: 0, team_b: 0, winner_name: '', ended_at: '' };
    document.officials.forEach((official) => {
      official.name = '';
      official.signature = 'absent';
    });

    expect(codes(document)).toEqual(expect.arrayContaining([
      'MISSING_ROSTER',
      'MISSING_SCORE_EVENTS',
      'MISSING_PERIOD_SCORE',
      'MISSING_END_TIME',
    ]));
    expect(validateLocal(document).issues.filter((entry) => entry.code === 'MISSING_PERIOD_SCORE')).toHaveLength(4);
    expect(codes(document)).not.toEqual(expect.arrayContaining([
      'MISSING_TABLE_PERSONNEL',
      'MISSING_REQUIRED_OFFICIAL',
      'MISSING_REQUIRED_SIGNATURE',
    ]));
  });
});

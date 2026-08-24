import rawTemplate from '../../../shared/template_definition.json';
import type {
  OfficialEntry,
  PlayerEntry,
  ScoresheetDocument,
  TeamEntry,
  TeamSide,
  TemplateDefinition,
} from '../types';

const players = (side: TeamSide): PlayerEntry[] =>
  Array.from({ length: 12 }, (_, index) => ({
    row: index + 1,
    license_number: `${side}${String(index + 1).padStart(3, '0')}`,
    name: `示例${side}${String(index + 1).padStart(2, '0')}`,
    jersey_number: String(index + 4),
    captain: index === 3,
    participation: index < 5 ? 'starter' : index < 10 ? 'substitute' : 'none',
    fouls: [],
    post_foul_markers: [],
  }));

const team = (side: TeamSide): TeamEntry => ({
  side,
  name: side === 'A' ? '示例学院甲' : '示例学院乙',
  players: players(side),
  timeouts: [],
  team_fouls: ([1, 2, 3, 4] as const).map((period) => ({ period, count: 0 })),
  coach_fouls: [],
  coach_post_foul_markers: [],
  assistant_coach_fouls: [],
  assistant_coach_post_foul_markers: [],
  head_coach: `教练${side}`,
  assistant_coach: `助教${side}`,
});

const officials: OfficialEntry[] = [
  'scorer',
  'assistant_scorer',
  'timer',
  'shot_clock_operator',
  'crew_chief',
  'umpire_1',
  'umpire_2',
  'protest_captain',
].map((role) => ({
  role: role as OfficialEntry['role'],
  name: role === 'protest_captain' ? '' : `示例${role}`,
  signature: role === 'protest_captain' ? 'absent' : 'present',
}));

export function makeDocument(id = 'synthetic-preview'): ScoresheetDocument {
  return {
    schema_version: '1.4.0',
    rules_profile: 'fiba_2024',
    id,
    revision: 0,
    template_id: 'pku-basketball-2019-v1',
    status: 'needs_review',
    created_at: '2026-08-18T00:00:00Z',
    updated_at: '2026-08-18T00:00:00Z',
    source: {
      original_filename: '',
      original_url: '',
      aligned_url: '',
      width: 0,
      height: 0,
      rotation: 0,
      corners: null,
    },
    header: {
      competition: '合成测试赛',
      game_number: 'SYN-TS',
      date: '2026-08-18',
      scheduled_time: '14:00',
      venue: '测试球馆',
      crew_chief: '主裁',
      umpire_1: '副裁一',
      umpire_2: '副裁二',
    },
    teams: [team('A'), team('B')],
    score_events: [
      { sequence: 1, team: 'A', period: 1, points: 1, cumulative_score: 1, scorer_jersey: '4', mark: 'filled_dot', scorer_circled: false, boundary: 'period_end', ink_role: 'q1_q3' },
      { sequence: 2, team: 'B', period: 1, points: 2, cumulative_score: 2, scorer_jersey: '4', mark: 'diagonal', scorer_circled: false, boundary: 'period_end', ink_role: 'q1_q3' },
      { sequence: 3, team: 'A', period: 2, points: 2, cumulative_score: 3, scorer_jersey: '5', mark: 'diagonal', scorer_circled: false, boundary: 'period_end', ink_role: 'q2_q4_ot' },
      { sequence: 4, team: 'B', period: 2, points: 3, cumulative_score: 5, scorer_jersey: '6', mark: 'diagonal', scorer_circled: true, boundary: 'game_end', ink_role: 'q2_q4_ot' },
      { sequence: 5, team: 'A', period: 2, points: 3, cumulative_score: 6, scorer_jersey: '7', mark: 'diagonal', scorer_circled: true, boundary: 'game_end', ink_role: 'q2_q4_ot' },
    ],
    stated_period_scores: [
      { period: 1, team_a: 1, team_b: 2 },
      { period: 2, team_a: 5, team_b: 3 },
      { period: 3, team_a: 0, team_b: 0 },
      { period: 4, team_a: 0, team_b: 0 },
    ],
    final_score: {
      team_a: 6,
      team_b: 5,
      winner_name: '示例学院甲',
      ended_at: '15:20',
    },
    officials,
    acknowledged_warnings: [],
  };
}

export function makeTemplate(): TemplateDefinition {
  return {
    ...rawTemplate,
    coordinate_system: 'pdf_points_top_left',
    ink_styles: { semantic_black: { default: '#11110f' } },
    cells: [],
  } as unknown as TemplateDefinition;
}

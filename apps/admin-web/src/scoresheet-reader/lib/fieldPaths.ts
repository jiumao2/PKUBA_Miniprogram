import type { ScoresheetDocument, TeamSide } from '../types';

const sideForTeamIndex = (index: string): TeamSide => index === '0' ? 'A' : 'B';
const scoreField = (side: string, cumulative: number, precise: boolean) =>
  `score.${side}.${String(cumulative).padStart(3, '0')}${precise ? '.edit' : ''}`;

const periodEndField = (
  document: ScoresheetDocument,
  side: TeamSide,
  period: number,
  precise: boolean,
) => {
  const event = document.score_events
    .filter((entry) => entry.team === side && entry.period === period)
    .sort((left, right) => right.cumulative_score - left.cumulative_score)[0];
  return event
    ? scoreField(side, event.cumulative_score, precise)
    : `summary.period.${period}.${side}`;
};

export function pathToField(
  path: string,
  document: ScoresheetDocument,
  precise = false,
): string {
  const header = path.match(/^\/header\/([^/]+)/);
  if (header) return `header.${header[1]}`;

  const semanticPlayer = path.match(/^\/teams\/(0|1)\/players\/row\/(\d+)(?:\/([^/]+))?/);
  if (semanticPlayer) {
    const side = sideForTeamIndex(semanticPlayer[1]);
    const base = `team.${side}.player.${String(Number(semanticPlayer[2])).padStart(2, '0')}`;
    const suffix: Record<string, string> = {
      license_number: 'license',
      name: 'name',
      jersey_number: 'jersey',
      participation: 'participation',
      fouls: 'foul.1',
      post_foul_markers: 'post_foul',
    };
    return semanticPlayer[3] && suffix[semanticPlayer[3]]
      ? `${base}.${suffix[semanticPlayer[3]]}`
      : base;
  }

  const player = path.match(/^\/teams\/(0|1)\/players\/(\d+)(?:\/([^/]+))?/);
  if (player) {
    const teamIndex = Number(player[1]);
    const playerIndex = Number(player[2]);
    const side = sideForTeamIndex(player[1]);
    const row = document.teams[teamIndex]?.players[playerIndex]?.row ?? playerIndex + 1;
    const base = `team.${side}.player.${String(row).padStart(2, '0')}`;
    const suffix: Record<string, string> = {
      license_number: 'license',
      name: 'name',
      jersey_number: 'jersey',
      participation: 'participation',
      fouls: 'foul.1',
      post_foul_markers: 'post_foul',
    };
    return player[3] && suffix[player[3]] ? `${base}.${suffix[player[3]]}` : base;
  }

  const team = path.match(/^\/teams\/(0|1)(?:\/([^/]+))?/);
  if (team) {
    const side = sideForTeamIndex(team[1]);
    const field = team[2] ?? '';
    if (field === 'name') return `team.${side}.name`;
    if (field === 'head_coach') return `team.${side}.head_coach`;
    if (field === 'assistant_coach') return `team.${side}.assistant_coach`;
    return `team.${side}.meta`;
  }

  const semanticEvent = path.match(/^\/score_events\/(A|B)\/cumulative\/(\d+)/);
  if (semanticEvent) return scoreField(semanticEvent[1], Number(semanticEvent[2]), precise);

  const semanticPeriodEnd = path.match(/^\/score_events\/(A|B)\/period\/(\d+)\/boundary/);
  if (semanticPeriodEnd) {
    return periodEndField(
      document,
      semanticPeriodEnd[1] as TeamSide,
      Number(semanticPeriodEnd[2]),
      precise,
    );
  }

  const legacyPeriodEnd = path.match(/^\/score_events\/(A|B)\/period_(\d+)_end/);
  if (legacyPeriodEnd) {
    return periodEndField(
      document,
      legacyPeriodEnd[1] as TeamSide,
      Number(legacyPeriodEnd[2]),
      precise,
    );
  }

  const event = path.match(/^\/score_events\/(\d+)/);
  if (event) {
    const score = document.score_events[Number(event[1])];
    if (score) return scoreField(score.team, score.cumulative_score, precise);
  }

  const recognizedEvent = path.match(/^\/score_events\/(A|B)\/(\d+)/);
  if (recognizedEvent) {
    const sideEvents = document.score_events.filter((entry) => entry.team === recognizedEvent[1]);
    const score = sideEvents[Number(recognizedEvent[2])];
    if (score) return scoreField(score.team, score.cumulative_score, precise);
  }

  const official = path.match(/^\/officials\/(\d+)/);
  if (official) {
    const entry = document.officials[Number(official[1])];
    if (entry) return `official.${entry.role}.name`;
    return 'officials';
  }
  const recognizedOfficial = path.match(/^\/officials\/([^/]+)/);
  if (recognizedOfficial) return `official.${recognizedOfficial[1]}.name`;

  const periodScore = path.match(/^\/stated_period_scores\/(\d+)(?:\/(A|B))?/);
  if (periodScore) return `summary.period.${periodScore[1]}.${periodScore[2] ?? 'A'}`;

  const finalScore = path.match(/^\/final_score\/(team_a|team_b|winner_name|ended_at)/);
  if (finalScore) {
    const fields: Record<string, string> = {
      team_a: 'summary.final.A',
      team_b: 'summary.final.B',
      winner_name: 'summary.winner',
      ended_at: 'summary.ended_at',
    };
    return fields[finalScore[1]];
  }
  if (path.startsWith('/final_score') || path.startsWith('/stated_period_scores')) return 'summary';
  return 'header';
}

export function describeRecognitionProblem(path: string, document: ScoresheetDocument): string {
  const semanticPlayer = path.match(/^\/teams\/(0|1)\/players\/row\/(\d+)(?:\/([^/]+))?/);
  const legacyPlayer = path.match(/^\/teams\/(0|1)\/players\/(\d+)(?:\/([^/]+))?/);
  if (semanticPlayer || legacyPlayer) {
    const match = semanticPlayer ?? legacyPlayer!;
    const side = sideForTeamIndex(match[1]);
    const indexOrRow = Number(match[2]);
    const row = semanticPlayer
      ? indexOrRow
      : document.teams[Number(match[1])]?.players[indexOrRow]?.row ?? indexOrRow + 1;
    const label: Record<string, string> = {
      name: '姓名', jersey_number: '球衣号码', participation: '参赛标记', fouls: '犯规',
    };
    return `${side} 队第 ${row} 行球员的${label[match[3] ?? ''] ?? '内容'}未能可靠确定`;
  }

  const coach = path.match(/^\/teams\/(0|1)\/(head_coach|assistant_coach)(?:\/([^/]+))?/);
  if (coach) {
    const side = sideForTeamIndex(coach[1]);
    const role = coach[2] === 'head_coach' ? '教练员' : '助理教练员';
    return `${side} 队${role}${coach[3] === 'fouls' ? '犯规' : '姓名'}未能可靠确定`;
  }

  const score = path.match(/^\/score_events\/(A|B)\/cumulative\/(\d+)(?:\/(\w+))?/);
  if (score) {
    if (score[3] === 'delta') return `${score[1]} 队累计 ${score[2]} 分候选事件的分差不合法`;
    if (score[3] === 'period') return `${score[1]} 队累计 ${score[2]} 分的所属节次未能可靠确定`;
    return `${score[1]} 队累计 ${score[2]} 分的得分号码未能可靠确定`;
  }

  const periodEnd = path.match(/^\/score_events\/(A|B)\/period\/(\d+)\/boundary/)
    ?? path.match(/^\/score_events\/(A|B)\/period_(\d+)_end/);
  if (periodEnd) {
    const label = Number(periodEnd[2]) <= 4 ? `第 ${periodEnd[2]} 节` : '决胜期';
    return `${periodEnd[1]} 队${label}结束累计分未能与书面节比分对应`;
  }

  const legacyScore = path.match(/^\/score_events\/(A|B)\/(\d+)(?:\/([^/]+))?/);
  if (legacyScore) {
    return `${legacyScore[1]} 队第 ${Number(legacyScore[2]) + 1} 个旧识别得分候选项需要人工核对`;
  }

  const periodScore = path.match(/^\/stated_period_scores\/(\d+)(?:\/(A|B))?/);
  if (periodScore) {
    const label = Number(periodScore[1]) <= 4 ? `第 ${periodScore[1]} 节` : '决胜期';
    return `${label}${periodScore[2] ? ` ${periodScore[2]} 队` : ''}书面得分未能可靠确定`;
  }

  const finalField = path.match(/^\/final_score\/(team_a|team_b|winner_name|ended_at)/);
  if (finalField) {
    const labels: Record<string, string> = {
      team_a: 'A 队最终比分', team_b: 'B 队最终比分', winner_name: '胜队名称', ended_at: '结束时间',
    };
    return `${labels[finalField[1]]}未能可靠确定`;
  }

  return `字段 ${path} 未能可靠确定`;
}

import {
  deepCloneDocument,
  deriveScoreEvents,
  OFFICIAL_ROLES,
  paperPlayerRows,
  setTimeoutMinute,
  sparsePlayerRows,
  teamBySide,
  type OfficialRole,
  type PlayerEntry,
  type RegulationPeriod,
  type ScoresheetDocument,
  type TeamEntry,
  type TeamSide,
  type TimeoutScope,
} from "@pkuba/scoresheet-domain";

export function mutateScoresheet(
  source: ScoresheetDocument,
  mutation: (draft: ScoresheetDocument) => void,
): ScoresheetDocument {
  const draft = deepCloneDocument(source);
  mutation(draft);
  return deriveScoreEvents(draft);
}

export function replaceTeam(
  document: ScoresheetDocument,
  side: TeamSide,
  update: (team: TeamEntry) => TeamEntry | void,
): ScoresheetDocument {
  return mutateScoresheet(document, (draft) => {
    const index = draft.teams.findIndex((team) => team.side === side);
    if (index < 0) throw new Error(`记录表缺少 ${side} 队`);
    const current = draft.teams[index];
    draft.teams[index] = update(current) ?? current;
  });
}

export function setPlayerRow(
  document: ScoresheetDocument,
  side: TeamSide,
  row: number,
  player: PlayerEntry,
): ScoresheetDocument {
  return replaceTeam(document, side, (team) => ({
    ...team,
    players: sparsePlayerRows([
      ...team.players.filter((candidate) => candidate.row !== row),
      { ...player, row },
    ]),
  }));
}

export function setTeamFoulCount(
  document: ScoresheetDocument,
  side: TeamSide,
  period: RegulationPeriod,
  count: number,
): ScoresheetDocument {
  return replaceTeam(document, side, (team) => ({
    ...team,
    team_fouls: [
      ...team.team_fouls.filter((entry) => entry.period !== period),
      { period, count: Math.max(0, Math.min(4, Math.trunc(count))) },
    ].sort((left, right) => left.period - right.period),
  }));
}

export function setTeamTimeoutMinute(
  document: ScoresheetDocument,
  side: TeamSide,
  scope: TimeoutScope,
  slot: number,
  minute: number | null,
): ScoresheetDocument {
  return replaceTeam(document, side, (team) => setTimeoutMinute(team, scope, slot, minute));
}

export function setOfficialName(
  document: ScoresheetDocument,
  role: OfficialRole,
  name: string,
): ScoresheetDocument {
  return mutateScoresheet(document, (draft) => {
    const current = draft.officials.find((entry) => entry.role === role);
    if (current) current.name = name;
    else draft.officials.push({ role, name, signature: "absent" });
    draft.officials.sort((left, right) => OFFICIAL_ROLES.indexOf(left.role) - OFFICIAL_ROLES.indexOf(right.role));
  });
}

export function setRecognitionPersonnel(
  document: ScoresheetDocument,
  names: string[],
): ScoresheetDocument {
  return mutateScoresheet(document, (draft) => {
    if (draft.recognition) draft.recognition.table_personnel = [...names];
  });
}

export function priorPlayerNames(document: ScoresheetDocument, side: TeamSide): string[] {
  const prior = side === "A" ? document.game_prior?.team_a : document.game_prior?.team_b;
  return prior?.player_names.filter(Boolean) ?? [];
}

export function compactRoster(document: ScoresheetDocument, side: TeamSide): PlayerEntry[] {
  return paperPlayerRows(teamBySide(document, side));
}

import {
  Button,
  Input,
  Picker,
  RootPortal,
  ScrollView,
  Switch,
  Text,
  View,
} from "@tarojs/components";
import Taro from "@tarojs/taro";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  deepCloneDocument,
  emptyPlayer,
  fiba2024FoulEditorOptions,
  isOrderedFoulSlotEnabled,
  isOrderedPostFoulSlotEnabled,
  isValidJerseyNumber,
  OFFICIAL_LABELS,
  OFFICIAL_ROLES,
  periodScore,
  removeScoreCell,
  SCORE_BLOCKS,
  scoreGridRow,
  semanticScoresheetPath,
  setOrderedFormalFoul,
  setOrderedPostFoul,
  setPeriodScore,
  setScoreCell,
  teamBySide,
  TIMEOUT_SCOPE_LABELS,
  TIMEOUT_SLOT_COUNTS,
  timeoutMinute,
  type FoulEntry,
  type GamePeriod,
  type PlayerEntry,
  type RegulationPeriod,
  type ScoreEvent,
  type ScoresheetDocument,
  type ScoresheetRegion,
  type TeamEntry,
  type TeamSide,
  type TimeoutScope,
} from "@pkuba/scoresheet-domain";
import {
  addRecognitionPersonnel,
  compactRoster,
  mutateScoresheet,
  priorPlayerNames,
  removeRecognitionPersonnel,
  replaceTeam,
  sanitizeJerseyInput,
  setOfficialName,
  setPlayerRow,
  setTeamFoulCount,
  setTeamTimeoutMinute,
  updateRecognitionPersonnel,
} from "./editorModel";
import "./MobileStandardView.css";

export type MobileStepKey = ScoresheetRegion | "CLOSING" | "PUBLISH";

export interface MobileScoresheetIssue {
  id?: string;
  region: ScoresheetRegion;
  path: string;
  message: string;
  severity: string;
}

interface MobileStandardViewProps {
  document: ScoresheetDocument;
  step: MobileStepKey;
  readOnly: boolean;
  issues: MobileScoresheetIssue[];
  selectedScoreId: string;
  onSelectScore: (key: string) => void;
  onChange: (document: ScoresheetDocument, immediate?: boolean) => void;
  onLocateIssue?: (anchor: string) => void;
}

export function MobileStandardView({
  document,
  step,
  readOnly,
  issues,
  selectedScoreId,
  onSelectScore,
  onChange,
  onLocateIssue,
}: MobileStandardViewProps) {
  const stepIssues = issues.filter((issue) => regionsForStep(step).includes(issue.region));
  const issueHeader = step !== "RUNNING_SCORE" && step !== "PUBLISH" && stepIssues.length > 0
    ? <StepIssues document={document} issues={stepIssues} onLocate={onLocateIssue} />
    : null;

  if (step === "SOURCE_GAME") {
    return <View className="canonical-step" id="step-source-game-top">{issueHeader}<GameEditor document={document} issues={stepIssues} onChange={onChange} readOnly={readOnly} /></View>;
  }
  if (step === "TEAM_A" || step === "TEAM_B") {
    const side = step === "TEAM_A" ? "A" : "B";
    return <View className="canonical-step" id={`step-team-${side.toLowerCase()}-top`}>{issueHeader}<TeamEditor document={document} issues={stepIssues} onChange={onChange} readOnly={readOnly} side={side} /></View>;
  }
  if (step === "RUNNING_SCORE") {
    return (
      <View className="canonical-step score-step" id="step-running-score-top">
        <PaperScoreGrid document={document} issues={stepIssues} onChange={onChange} onSelect={onSelectScore} readOnly={readOnly} selectedKey={selectedScoreId} />
      </View>
    );
  }
  if (step === "CLOSING") {
    return <View className="canonical-step" id="step-closing-top">{issueHeader}<ClosingEditor document={document} issues={stepIssues} onChange={onChange} readOnly={readOnly} /></View>;
  }
  return <View className="canonical-step" id="step-publish-top" />;
}

function regionsForStep(step: MobileStepKey): ScoresheetRegion[] {
  if (step === "CLOSING") return ["SUMMARY", "OFFICIALS"];
  if (step === "PUBLISH") return [];
  return [step];
}

function StepIssues({ document, issues, onLocate }: {
  document: ScoresheetDocument;
  issues: MobileScoresheetIssue[];
  onLocate?: (anchor: string) => void;
}) {
  const first = issues[0];
  return (
    <View className="step-issues" onClick={() => onLocate?.(issueAnchor(first, document))}>
      <View><Text>{issues.length} 处问题</Text><Text>{semanticScoresheetPath(first.path, document)}：{first.message}</Text></View>
      <Text>定位</Text>
    </View>
  );
}

function GameEditor({ document, readOnly, onChange, issues }: Pick<MobileStandardViewProps, "document" | "readOnly" | "onChange" | "issues">) {
  const teamA = teamBySide(document, "A");
  const teamB = teamBySide(document, "B");
  const locked = [
    ["competition", "赛事", document.header.competition],
    ["date", "日期", document.header.date],
    ["scheduled_time", "开赛时间", document.header.scheduled_time],
    ["venue", "场地", document.header.venue],
    ["team_a", "A 队", teamA.name],
    ["team_b", "B 队", teamB.name],
  ];
  const officials: Array<["crew_chief" | "umpire_1" | "umpire_2", string]> = [
    ["crew_chief", "主裁"],
    ["umpire_1", "第一副裁"],
    ["umpire_2", "第二副裁"],
  ];
  return (
    <View className="canonical-card">
      {locked.map(([field, label, value]) => (
        <View className={`canonical-locked-row ${hasIssue(issues, `/header/${field}`) ? "invalid" : ""}`} id={`game-${field}`} key={field}>
          <Text>{label}</Text><Text>{value || "—"}</Text>
        </View>
      ))}
      <View className="canonical-section-title"><Text>赛前裁判</Text></View>
      {officials.map(([field, label]) => (
        <LabeledInput
          disabled={readOnly}
          id={`game-${field}`}
          invalid={hasIssue(issues, `/header/${field}`)}
          key={field}
          label={label}
          value={document.header[field]}
          onChange={(value) => onChange(mutateScoresheet(document, (draft) => { draft.header[field] = value; }))}
        />
      ))}
    </View>
  );
}

function TeamEditor({ document, side, readOnly, onChange, issues }: Pick<MobileStandardViewProps, "document" | "readOnly" | "onChange" | "issues"> & { side: TeamSide }) {
  const team = teamBySide(document, side);
  const players = compactRoster(document, side);
  const [playerRow, setPlayerRowNumber] = useState<number | null>(null);
  const priorNames = priorPlayerNames(document, side);
  const teamIndex = side === "A" ? 0 : 1;
  const updateTeam = (update: (team: TeamEntry) => TeamEntry | void, immediate = false) => onChange(replaceTeam(document, side, update), immediate);
  useEffect(() => { if (readOnly) setPlayerRowNumber(null); }, [readOnly]);
  return (
    <View className="canonical-team-card">
      <View className="canonical-team-heading"><Text>{side} 队</Text><Text>{team.name}</Text></View>
      <View className="canonical-subsection" id={`team-${side}-timeouts`}><Text>暂停分钟</Text></View>
      <View className={`timeout-groups ${hasIssue(issues, `/teams/${teamIndex}/timeouts`) ? "invalid" : ""}`}>
        {(Object.keys(TIMEOUT_SLOT_COUNTS) as TimeoutScope[]).map((scope) => (
          <View className="timeout-group" key={scope}>
            <Text>{TIMEOUT_SCOPE_LABELS[scope]}</Text>
            <View>{Array.from({ length: TIMEOUT_SLOT_COUNTS[scope] }, (_, index) => index + 1).map((slot) => (
              <TimeoutCell
                disabled={readOnly}
                invalid={hasIssue(issues, `/teams/${teamIndex}/timeouts`)}
                key={slot}
                minute={timeoutMinute(team, scope, slot)}
                onChange={(minute) => onChange(setTeamTimeoutMinute(document, side, scope, slot, minute), true)}
                scope={scope}
                slot={slot}
              />
            ))}</View>
          </View>
        ))}
      </View>

      <View className="canonical-subsection" id={`team-${side}-team-fouls`}><Text>全队犯规</Text></View>
      <View className={`team-foul-grid ${hasIssue(issues, `/teams/${teamIndex}/team_fouls`) ? "invalid" : ""}`}>
        {([1, 2, 3, 4] as RegulationPeriod[]).map((period) => (
          <CompactStepper disabled={readOnly} key={period} label={`第 ${period} 节`} max={4} value={team.team_fouls.find((entry) => entry.period === period)?.count ?? 0} onChange={(count) => onChange(setTeamFoulCount(document, side, period, count), true)} />
        ))}
      </View>

      <View id={`team-${side}-coaches`}>
        <CoachEditor
          disabled={readOnly}
          fouls={team.coach_fouls}
          label="教练员"
          name={team.head_coach}
          postFouls={team.coach_post_foul_markers}
          onName={(name) => updateTeam((draftTeam) => { draftTeam.head_coach = name; })}
          onUpdate={(fouls, postFouls) => updateTeam((draftTeam) => { draftTeam.coach_fouls = fouls; draftTeam.coach_post_foul_markers = postFouls; }, true)}
        />
        <CoachEditor
          disabled={readOnly}
          fouls={team.assistant_coach_fouls}
          label="助理教练员"
          name={team.assistant_coach}
          postFouls={team.assistant_coach_post_foul_markers}
          onName={(name) => updateTeam((draftTeam) => { draftTeam.assistant_coach = name; })}
          onUpdate={(fouls, postFouls) => updateTeam((draftTeam) => { draftTeam.assistant_coach_fouls = fouls; draftTeam.assistant_coach_post_foul_markers = postFouls; }, true)}
        />
      </View>

      <View className="canonical-subsection roster-heading"><Text>队员名单</Text><Text>12 行</Text></View>
      <View className="roster-list">
        {players.map((player) => (
          <View className={`roster-row ${playerHasIssue(issues, document, side, player.row) ? "invalid" : ""}`} id={`team-${side}-player-${player.row}`} key={player.row} onClick={() => { if (!readOnly) setPlayerRowNumber(player.row); }}>
            <Text className="roster-paper-row">{player.row}</Text>
            <Text className="roster-jersey">{player.jersey_number || "—"}</Text>
            <View className="roster-name"><Text>{player.name || "空白"}</Text><Text>{participationLabel(player.participation)}{player.captain ? " · 队长" : ""}</Text></View>
            <Text className="roster-edit">{readOnly ? "" : "编辑"}</Text>
          </View>
        ))}
      </View>
      {playerRow !== null && (
        <PlayerDrawer
          disabled={readOnly}
          player={players[playerRow - 1]}
          priorNames={priorNames}
          onChange={(player, immediate) => onChange(setPlayerRow(document, side, playerRow, player), immediate)}
          onClose={() => setPlayerRowNumber(null)}
        />
      )}
    </View>
  );
}

function TimeoutCell({ scope, slot, minute, disabled, invalid, onChange }: {
  scope: TimeoutScope;
  slot: number;
  minute: number | null;
  disabled: boolean;
  invalid: boolean;
  onChange: (minute: number | null) => void;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  const choices = ["清空", ...Array.from({ length: 11 }, (_, value) => `${value} 分钟`)];
  return (
    <>
      <Button className={`${minute === null ? "timeout-cell" : "timeout-cell filled"} ${invalid ? "invalid" : ""}`} disabled={disabled} onClick={() => setOpen(true)}>{minute === null ? "—" : `${minute}′`}</Button>
      {open && (
        <Drawer title={`${TIMEOUT_SCOPE_LABELS[scope]} · 第 ${slot} 格`} onClose={() => setOpen(false)}>
          <Picker disabled={disabled} mode="selector" range={choices} value={minute === null ? 0 : minute + 1} onChange={(event) => { const index = Number(event.detail.value); onChange(index === 0 ? null : index - 1); }}>
            <View className="drawer-choice"><Text>暂停分钟</Text><Text>{minute === null ? "清空" : `${minute} 分钟`}</Text></View>
          </Picker>
          <View className="timeout-adjust">
            <Button disabled={disabled || minute === 0} onClick={() => onChange(Math.max(0, (minute ?? 0) - 1))}>−</Button>
            <Text>{minute === null ? "—" : minute}</Text>
            <Button disabled={disabled || minute === 10} onClick={() => onChange(Math.min(10, (minute ?? -1) + 1))}>＋</Button>
          </View>
          <Button className="drawer-danger full" disabled={disabled || minute === null} onClick={() => onChange(null)}>清空</Button>
        </Drawer>
      )}
    </>
  );
}

function CoachEditor({ label, name, fouls, postFouls, disabled, onName, onUpdate }: {
  label: string;
  name: string;
  fouls: FoulEntry[];
  postFouls: FoulEntry[];
  disabled: boolean;
  onName: (name: string) => void;
  onUpdate: (fouls: FoulEntry[], postFouls: FoulEntry[]) => void;
}) {
  return (
    <View className="coach-editor">
      <LabeledInput disabled={disabled} label={label} onChange={onName} value={name} />
      <View className="foul-row">
        <Text>犯规</Text>
        {Array.from({ length: 3 }, (_, index) => index + 1).map((slot) => (
          <FoulCell disabled={disabled || !isOrderedFoulSlotEnabled(fouls, slot)} group="coach" key={slot} slot={slot} value={fouls.find((entry) => entry.slot === slot)} onChange={(entry) => { const next = setOrderedFormalFoul(fouls, postFouls, slot, entry); onUpdate(next.formalEntries, next.postEntries); }} />
        ))}
        <Text className="post-foul-label">附加</Text>
        {[1, 2].map((slot) => (
          <FoulCell disabled={disabled || !isOrderedPostFoulSlotEnabled(fouls, postFouls, 3, slot)} group="post" key={slot} slot={slot} value={postFouls.find((entry) => entry.slot === slot)} onChange={(entry) => onUpdate(fouls, setOrderedPostFoul(fouls, postFouls, 3, slot, entry))} />
        ))}
      </View>
    </View>
  );
}

function PlayerDrawer({ player, priorNames, disabled, onChange, onClose }: {
  player: PlayerEntry;
  priorNames: string[];
  disabled: boolean;
  onChange: (player: PlayerEntry, immediate?: boolean) => void;
  onClose: () => void;
}) {
  const nameOptions = useMemo(() => Array.from(new Set(["", ...priorNames, ...(player.name && !priorNames.includes(player.name) ? [player.name] : [])])), [player.name, priorNames]);
  const jerseyValid = isValidJerseyNumber(player.jersey_number);
  const set = <K extends keyof PlayerEntry>(key: K, value: PlayerEntry[K], immediate = false) => onChange({ ...player, [key]: value }, immediate);
  const clear = async () => {
    const result = await Taro.showModal({ title: "清空队员行", content: `确认清空第 ${player.row} 行的全部内容？`, confirmText: "确认清空" });
    if (!result.confirm) return;
    onChange(emptyPlayer(player.row), true);
    onClose();
  };
  return (
    <Drawer title={`队员第 ${player.row} 行`} onClose={onClose}>
      <LabeledInput disabled={disabled} label="证件号码" value={player.license_number} onChange={(value) => set("license_number", value)} />
      {priorNames.length > 0 ? (
        <Picker disabled={disabled} mode="selector" range={nameOptions.map((name) => name || "留空")} value={Math.max(0, nameOptions.indexOf(player.name))} onChange={(event) => set("name", nameOptions[Number(event.detail.value)] ?? "", true)}>
          <View className="drawer-choice"><Text>姓名</Text><Text>{player.name || "留空"}</Text></View>
        </Picker>
      ) : <LabeledInput disabled={disabled} label="姓名" value={player.name} onChange={(value) => set("name", value)} />}
      <LabeledInput
        disabled={disabled}
        inputType="number"
        invalid={!jerseyValid}
        label="球衣号码"
        value={player.jersey_number}
        onChange={(value) => set("jersey_number", sanitizeJerseyInput(value))}
      />
      <Picker disabled={disabled} mode="selector" range={["未上场", "替补", "首发"]} value={Math.max(0, ["none", "substitute", "starter"].indexOf(player.participation))} onChange={(event) => set("participation", (["none", "substitute", "starter"] as const)[Number(event.detail.value)] ?? "none", true)}>
        <View className="drawer-choice"><Text>上场状态</Text><Text>{participationLabel(player.participation)}</Text></View>
      </Picker>
      <View className="drawer-choice"><Text>队长</Text><Switch checked={player.captain} color="#b52d28" disabled={disabled} onChange={(event) => set("captain", event.detail.value, true)} /></View>
      <View className="player-foul-editor">
        <Text>犯规格</Text>
        <View className="foul-row">
          {[1, 2, 3, 4, 5].map((slot) => (
            <FoulCell disabled={disabled || !isOrderedFoulSlotEnabled(player.fouls, slot)} group="player" key={slot} slot={slot} value={player.fouls.find((entry) => entry.slot === slot)} onChange={(entry) => { const next = setOrderedFormalFoul(player.fouls, player.post_foul_markers, slot, entry); onChange({ ...player, fouls: next.formalEntries, post_foul_markers: next.postEntries }, true); }} />
          ))}
        </View>
        <Text>附加标记</Text>
        <View className="foul-row">
          {[1, 2].map((slot) => (
            <FoulCell disabled={disabled || !isOrderedPostFoulSlotEnabled(player.fouls, player.post_foul_markers, 5, slot)} group="post" key={slot} slot={slot} value={player.post_foul_markers.find((entry) => entry.slot === slot)} onChange={(entry) => onChange({ ...player, post_foul_markers: setOrderedPostFoul(player.fouls, player.post_foul_markers, 5, slot, entry) }, true)} />
          ))}
        </View>
      </View>
      {!jerseyValid && <Text className="field-error">号码只能为空、0、00 或 1–99。</Text>}
      <Button className="drawer-danger full" disabled={disabled} onClick={() => void clear()}>清空本行</Button>
    </Drawer>
  );
}

function FoulCell({ group, slot, value, disabled = false, onChange }: {
  group: "player" | "coach" | "post";
  slot: number;
  value?: FoulEntry;
  disabled?: boolean;
  onChange: (value: FoulEntry | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const choices = foulChoices(group);
  const periods: Array<GamePeriod | null> = [null, 1, 2, 3, 4, 5];
  const periodLabels = ["不填写", "第 1 节", "第 2 节", "第 3 节", "第 4 节", "决胜期"];
  const label = foulLabel(value);
  const currentIndex = Math.max(0, choices.findIndex((choice) => choice.label === label));
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  return (
    <>
      <Button className={value ? "foul-cell filled" : "foul-cell"} disabled={disabled} onClick={() => setOpen(true)}>
        <Text>{label}</Text>{value?.period ? <Text>{value.period === 5 ? "加" : `第${value.period}节`}</Text> : null}
      </Button>
      {open && (
        <Drawer title="犯规标记" onClose={() => setOpen(false)}>
          <Picker disabled={disabled} mode="selector" range={choices.map((choice) => choice.label)} value={currentIndex} onChange={(event) => {
            const next = choices[Number(event.detail.value)]?.value;
            onChange(next ? { ...next, slot, period: value?.period ?? null } : null);
          }}>
            <View className="drawer-choice"><Text>代码与下标</Text><Text>{label}</Text></View>
          </Picker>
          <Picker disabled={disabled || !value} mode="selector" range={periodLabels} value={Math.max(0, periods.indexOf(value?.period ?? null))} onChange={(event) => value && onChange({ ...value, period: periods[Number(event.detail.value)] ?? null })}>
            <View className="drawer-choice"><Text>发生节次</Text><Text>{value ? periodLabels[Math.max(0, periods.indexOf(value.period))] : "请先选择标记"}</Text></View>
          </Picker>
          {value ? <Button className="drawer-danger full" disabled={disabled} onClick={() => { onChange(null); setOpen(false); }}>清空此格</Button> : null}
        </Drawer>
      )}
    </>
  );
}

function PaperScoreGrid({ document, issues, readOnly, onChange, selectedKey, onSelect }: {
  document: ScoresheetDocument;
  issues: MobileScoresheetIssue[];
  readOnly: boolean;
  onChange: (document: ScoresheetDocument, immediate?: boolean) => void;
  selectedKey: string;
  onSelect: (key: string) => void;
}) {
  const [blockIndex, setBlockIndex] = useState(0);
  const [drawerCell, setDrawerCell] = useState<{ side: TeamSide; cumulative: number } | null>(null);
  const [issueIndex, setIssueIndex] = useState(0);
  const [scrollAnchor, setScrollAnchor] = useState("score-row-1");
  const block = SCORE_BLOCKS[blockIndex];
  const byCell = useMemo(() => new Map(document.score_events.map((event) => [scoreKey(event.team, event.cumulative_score), event])), [document.score_events]);
  const issueCells = useMemo(() => scoreIssueCells(document, issues), [document, issues]);
  const selected = selectedKey ? byCell.get(selectedKey) ?? null : null;
  const selectedLocation = selected ? scoreGridRow(selected.cumulative_score) : null;
  useEffect(() => {
    if (!selectedLocation || !selected) return;
    setBlockIndex(selectedLocation.block);
    setScrollAnchor(`score-row-${selected.cumulative_score}`);
  }, [selected, selectedLocation?.block]);
  useEffect(() => { if (readOnly) setDrawerCell(null); }, [readOnly]);
  const selectBlock = (index: number) => {
    setBlockIndex(index);
    setScrollAnchor(`score-row-${SCORE_BLOCKS[index].start}`);
  };
  const locateIssue = (offset: number) => {
    if (issueCells.length === 0) return;
    const nextIndex = (issueIndex + offset + issueCells.length) % issueCells.length;
    const target = issueCells[nextIndex];
    const location = scoreGridRow(target.cumulative);
    if (!location) return;
    setIssueIndex(nextIndex);
    setBlockIndex(location.block);
    setScrollAnchor(`score-row-${target.cumulative}`);
    onSelect(scoreKey(target.side, target.cumulative));
  };
  return (
    <View className="paper-score-card">
      {issues.length > 0 ? (
        <View className="score-issue-nav">
          <Text>{issueCells.length > 0 ? `问题 ${Math.min(issueIndex + 1, issueCells.length)}/${issueCells.length}` : `${issues.length} 处问题`}</Text>
          {issueCells.length > 0 ? <View><Button onClick={() => locateIssue(-1)}>上一处</Button><Button onClick={() => locateIssue(1)}>下一处</Button></View> : null}
        </View>
      ) : null}
      <ScrollView className="score-block-tabs" scrollX showScrollbar={false}>
        <View>{SCORE_BLOCKS.map((item, index) => <Button className={index === blockIndex ? "active" : ""} key={item.key} onClick={() => selectBlock(index)}>{item.key}</Button>)}</View>
      </ScrollView>
      <View className="score-grid-heading"><Text>累计</Text><Text>A 队</Text><Text>B 队</Text></View>
      <ScrollView className="score-grid-scroll" scrollIntoView={scrollAnchor} scrollWithAnimation scrollY enhanced showScrollbar={false}>
        {Array.from({ length: 40 }, (_, index) => block.start + index).map((score) => (
          <View className="score-grid-row" id={`score-row-${score}`} key={score}>
            <Text>{score}</Text>
            {(["A", "B"] as TeamSide[]).map((side) => {
              const key = scoreKey(side, score);
              const event = byCell.get(key);
              const eventIndex = event ? document.score_events.indexOf(event) : -1;
              const invalid = Boolean(event && ((event.points ?? 0) > 3 || issues.some((issue) => issue.path.startsWith(`/score_events/${eventIndex}`))));
              return (
                <View className={`score-cell ${invalid ? "invalid" : ""} ${selectedKey === key ? "selected" : ""}`} key={side} onClick={() => {
                  if (readOnly && !event) return;
                  onSelect(key);
                  setDrawerCell({ side, cumulative: score });
                }}>
                  {event ? <><Text className="score-jersey">{event.scorer_jersey || "?"}</Text><Text className="score-mark">{scoreMark(event)}</Text><Text className="score-meta">{event.points && event.points > 3 ? `+${event.points}` : scoreMeta(event)}</Text></> : <Text className="score-empty">＋</Text>}
                </View>
              );
            })}
          </View>
        ))}
      </ScrollView>
      {drawerCell ? (
        <ScoreCellDrawer cell={drawerCell} document={document} event={byCell.get(scoreKey(drawerCell.side, drawerCell.cumulative)) ?? null} readOnly={readOnly} onClose={() => { setDrawerCell(null); onSelect(""); }} onChange={onChange} />
      ) : null}
    </View>
  );
}

function ScoreCellDrawer({ document, event, cell, readOnly, onChange, onClose }: {
  document: ScoresheetDocument;
  event: ScoreEvent | null;
  cell: { side: TeamSide; cumulative: number };
  readOnly: boolean;
  onChange: (document: ScoresheetDocument, immediate?: boolean) => void;
  onClose: () => void;
}) {
  const roster = teamBySide(document, cell.side).players.filter((player) => player.jersey_number);
  const rosterHasCurrent = Boolean(event?.scorer_jersey && roster.some((player) => player.jersey_number === event.scorer_jersey));
  const options = event?.scorer_jersey && !rosterHasCurrent
    ? [{ row: -1, jersey_number: event.scorer_jersey, name: "名单外号码" }, ...roster]
    : roster;
  const currentIndex = event?.scorer_jersey ? Math.max(0, options.findIndex((player) => player.jersey_number === event.scorer_jersey)) : 0;
  const choose = (index: number) => {
    const player = options[index];
    if (!player || (event && player.jersey_number === event.scorer_jersey)) return;
    const next = deepCloneDocument(document);
    setScoreCell(next, cell.side, cell.cumulative, player.jersey_number);
    onChange(next, true);
  };
  return (
    <Drawer title={`${cell.side} 队 · 累计 ${cell.cumulative} 分`} onClose={onClose}>
      {options.length > 0 ? (
        <Picker disabled={readOnly} mode="selector" range={options.map((player) => `${player.jersey_number} ${player.name}`.trim())} value={currentIndex} onChange={(change) => choose(Number(change.detail.value))}>
          <View className="drawer-choice"><Text>球员号码</Text><Text>{event?.scorer_jersey || "请选择"}</Text></View>
        </Picker>
      ) : <Text className="drawer-empty">请先在球队名单填写球员号码。</Text>}
      {event ? (
        <View className="derived-list">
          <View className={(event.points ?? 0) > 3 ? "invalid" : ""}><Text>本次得分</Text><Text>{event.points ?? "—"} 分</Text></View>
          <View><Text>节次</Text><Text>{event.period === 5 ? "决胜期" : `第 ${event.period} 节`}</Text></View>
          <View><Text>结束标记</Text><Text>{event.boundary === "game_end" ? "终场" : event.boundary === "period_end" ? "节末" : "普通得分"}</Text></View>
          {!readOnly ? <Button className="drawer-danger full" onClick={() => { const next = deepCloneDocument(document); removeScoreCell(next, cell.side, cell.cumulative); onChange(next, true); onClose(); }}>删除号码</Button> : null}
        </View>
      ) : null}
    </Drawer>
  );
}

function ClosingEditor({ document, readOnly, onChange, issues }: Pick<MobileStandardViewProps, "document" | "readOnly" | "onChange" | "issues">) {
  const [newPersonnelName, setNewPersonnelName] = useState<string | null>(null);
  const updatePeriod = (period: GamePeriod, side: TeamSide, value: number) => {
    const next = deepCloneDocument(document);
    setPeriodScore(next, period, side, value);
    onChange(next, true);
  };
  const tablePersonnel = document.recognition?.table_personnel ?? [];
  const finishAddingPersonnel = (rawName = newPersonnelName ?? "") => {
    const name = rawName.trim();
    if (name) {
      onChange(addRecognitionPersonnel(document, name), true);
    }
    setNewPersonnelName(null);
  };
  useEffect(() => {
    if (readOnly) setNewPersonnelName(null);
  }, [readOnly]);
  return (
    <View className="closing-card">
      <View className="closing-heading"><Text>节次</Text><Text>A 队</Text><Text>B 队</Text></View>
      {([1, 2, 3, 4, 5] as GamePeriod[]).map((period, index) => {
        const score = periodScore(document, period);
        return (
          <View className={`closing-row ${hasIssue(issues, `/stated_period_scores/${index}`) ? "invalid" : ""}`} id={`closing-period-${period}`} key={period}>
            <Text>{period === 5 ? "决胜期合计" : `第 ${period} 节`}</Text>
            <ScoreNumber disabled={readOnly} value={score.team_a} onChange={(value) => updatePeriod(period, "A", value)} />
            <ScoreNumber disabled={readOnly} value={score.team_b} onChange={(value) => updatePeriod(period, "B", value)} />
          </View>
        );
      })}
      <View className={`final-summary ${hasIssue(issues, "/final_score") ? "invalid" : ""}`} id="closing-final-score">
        <Text>最终比分</Text><Text>{document.final_score.team_a} : {document.final_score.team_b}</Text>
        <Text>{document.final_score.winner_name ? `胜队：${document.final_score.winner_name}` : "平分，不能发布"}</Text>
      </View>
      <View className="ended-time-row" id="closing-ended-at">
        <Picker disabled={readOnly} mode="time" value={document.final_score.ended_at || "00:00"} onChange={(event) => onChange(mutateScoresheet(document, (draft) => { draft.final_score.ended_at = String(event.detail.value); }), true)}>
          <View className="canonical-input-row"><Text>比赛结束时间</Text><Text>{document.final_score.ended_at || "请选择"}</Text></View>
        </Picker>
        {document.final_score.ended_at ? <Button disabled={readOnly} onClick={() => onChange(mutateScoresheet(document, (draft) => { draft.final_score.ended_at = ""; }), true)}>清空</Button> : null}
      </View>

      <View className="personnel-section" id="closing-table-personnel">
        <View className="canonical-section-title"><Text>记录台人员 · 不分岗位</Text></View>
        {tablePersonnel.map((name, index) => (
          <PersonnelRow
            document={document}
            index={index}
            key={index}
            name={name}
            onChange={onChange}
            readOnly={readOnly}
          />
        ))}
        {newPersonnelName !== null ? (
          <View className="personnel-row personnel-draft-row">
            <Input
              adjustPosition
              confirmType="done"
              cursorSpacing={140}
              disabled={readOnly}
              focus
              placeholder="输入姓名"
              value={newPersonnelName}
              onConfirm={(event) => finishAddingPersonnel(event.detail.value)}
              onInput={(event) => setNewPersonnelName(event.detail.value)}
            />
            <Button
              className={newPersonnelName.trim() ? "confirm" : ""}
              disabled={readOnly}
              onClick={() => finishAddingPersonnel()}
            >{newPersonnelName.trim() ? "添加" : "取消"}</Button>
          </View>
        ) : null}
        <Button className="personnel-add" disabled={readOnly || newPersonnelName !== null} onClick={() => setNewPersonnelName("")}>添加人员</Button>
      </View>

      <View className="personnel-section" id="closing-officials">
        <View className="canonical-section-title"><Text>工作人员</Text></View>
        {OFFICIAL_ROLES.map((role) => (
          <LabeledInput disabled={readOnly} key={role} label={OFFICIAL_LABELS[role]} value={document.officials.find((entry) => entry.role === role)?.name ?? ""} onChange={(value) => onChange(setOfficialName(document, role, value))} />
        ))}
      </View>
    </View>
  );
}

function PersonnelRow({ document, index, name, onChange, readOnly }: {
  document: ScoresheetDocument;
  index: number;
  name: string;
  onChange: MobileStandardViewProps["onChange"];
  readOnly: boolean;
}) {
  const update = (value: string) => {
    onChange(updateRecognitionPersonnel(document, index, value));
  };
  const remove = () => {
    onChange(removeRecognitionPersonnel(document, index), true);
  };
  return (
    <View className="personnel-row" data-personnel-index={index}>
      <Input
        adjustPosition
        cursorSpacing={140}
        disabled={readOnly}
        value={name}
        onInput={(event) => update(event.detail.value)}
      />
      <Button className="personnel-remove" data-personnel-index={index} disabled={readOnly} onClick={remove}>删除</Button>
    </View>
  );
}

function ScoreNumber({ value, disabled, onChange }: { value: number; disabled: boolean; onChange: (value: number) => void }) {
  const choices = Array.from({ length: 161 }, (_, index) => String(index));
  return (
    <View className="score-number">
      <Button disabled={disabled || value <= 0} onClick={() => onChange(value - 1)}>−</Button>
      <Picker disabled={disabled} mode="selector" range={choices} value={value} onChange={(event) => onChange(Number(event.detail.value))}><Text>{value}</Text></Picker>
      <Button disabled={disabled || value >= 160} onClick={() => onChange(value + 1)}>＋</Button>
    </View>
  );
}

function CompactStepper({ label, value, disabled, max, onChange }: { label: string; value: number; disabled: boolean; max: number; onChange: (value: number) => void }) {
  return <View className="compact-stepper"><Text>{label}</Text><Button disabled={disabled || value <= 0} onClick={() => onChange(value - 1)}>−</Button><Text>{value}</Text><Button disabled={disabled || value >= max} onClick={() => onChange(value + 1)}>＋</Button></View>;
}

function LabeledInput({ label, value, id, disabled = false, invalid = false, inputType = "text", onChange }: {
  label: string;
  value: string;
  id?: string;
  disabled?: boolean;
  invalid?: boolean;
  inputType?: "text" | "number" | "idcard" | "digit" | "nickname";
  onChange: (value: string) => void;
}) {
  return <View className={`canonical-input-row ${invalid ? "invalid" : ""}`} id={id}><Text>{label}</Text><Input adjustPosition cursorSpacing={140} disabled={disabled} type={inputType} value={value} onInput={(event) => onChange(event.detail.value)} /></View>;
}

function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <RootPortal>
      <View className="canonical-drawer-mask" onClick={onClose}>
        <View className="canonical-drawer" onClick={(event) => event.stopPropagation()}>
          <View className="canonical-drawer-heading"><Text>{title}</Text><Button onClick={onClose}>关闭</Button></View>
          <ScrollView className="canonical-drawer-body" scrollY enhanced showScrollbar={false}>{children}</ScrollView>
        </View>
      </View>
    </RootPortal>
  );
}

const SUBSCRIPT: Record<string, string> = { "1": "₁", "2": "₂", "3": "₃", c: "c" };

function foulChoices(group: "player" | "coach" | "post") {
  const result: Array<{ label: string; value: Omit<FoulEntry, "slot" | "period"> | null }> = [{ label: "空", value: null }];
  for (const option of fiba2024FoulEditorOptions(group === "post" ? "post_foul" : group)) {
    for (const suffix of option.allowedSuffixes) {
      result.push({
        label: `${option.code}${SUBSCRIPT[suffix] ?? ""}`,
        value: {
          code: option.code as FoulEntry["code"],
          free_throws: /^[123]$/.test(suffix) ? Number(suffix) : null,
          cancelled: suffix === "c",
          catalog_id: option.catalogId,
          mark_style: option.markStyle,
        },
      });
    }
  }
  return result;
}

function foulLabel(value?: FoulEntry) {
  if (!value) return "＋";
  const suffix = value.cancelled ? "c" : value.free_throws ? String(value.free_throws) : "";
  return `${value.code}${SUBSCRIPT[suffix] ?? ""}`;
}

function participationLabel(value: PlayerEntry["participation"]) {
  return value === "starter" ? "首发" : value === "substitute" ? "替补" : "未上场";
}

function scoreKey(side: TeamSide, cumulative: number) {
  return `${side}:${cumulative}`;
}

function scoreMark(event: ScoreEvent) {
  if (event.points === 1) return "●";
  if (event.points === 2) return "╱";
  if (event.points === 3) return "◯";
  return "—";
}

function scoreMeta(event: ScoreEvent) {
  const period = event.period === 5 ? "加时" : `第${event.period}节`;
  const boundary = event.boundary === "game_end" ? "终" : event.boundary === "period_end" ? "末" : "";
  return `${period}${boundary}`;
}

function hasIssue(issues: MobileScoresheetIssue[], prefix: string): boolean {
  return issues.some((issue) => issue.path === prefix || issue.path.startsWith(`${prefix}/`));
}

function playerHasIssue(issues: MobileScoresheetIssue[], document: ScoresheetDocument, side: TeamSide, row: number): boolean {
  const teamIndex = side === "A" ? 0 : 1;
  return issues.some((issue) => {
    const match = issue.path.match(new RegExp(`^/teams/${teamIndex}/players/(\\d+)`));
    if (!match) return false;
    const index = Number(match[1]);
    return (document.teams[teamIndex]?.players[index]?.row ?? index + 1) === row;
  });
}

function scoreIssueCells(document: ScoresheetDocument, issues: MobileScoresheetIssue[]): Array<{ side: TeamSide; cumulative: number }> {
  const result = new Map<string, { side: TeamSide; cumulative: number }>();
  for (const issue of issues) {
    const match = issue.path.match(/^\/score_events\/(\d+)/);
    const event = match ? document.score_events[Number(match[1])] : undefined;
    if (event) result.set(scoreKey(event.team, event.cumulative_score), { side: event.team, cumulative: event.cumulative_score });
  }
  return [...result.values()].sort((left, right) => left.cumulative - right.cumulative || left.side.localeCompare(right.side));
}

function issueAnchor(issue: MobileScoresheetIssue, document: ScoresheetDocument): string {
  const parts = issue.path.split("/").filter(Boolean);
  if (parts[0] === "header") return `game-${parts[1] ?? "competition"}`;
  if (parts[0] === "teams") {
    const teamIndex = Number(parts[1]);
    const side = teamIndex === 1 ? "B" : "A";
    if (parts[2] === "players") {
      const playerIndex = Number(parts[3]);
      const row = document.teams[teamIndex]?.players[playerIndex]?.row ?? playerIndex + 1;
      return `team-${side}-player-${row}`;
    }
    if (parts[2] === "timeouts") return `team-${side}-timeouts`;
    if (parts[2] === "team_fouls") return `team-${side}-team-fouls`;
    return `team-${side}-coaches`;
  }
  if (parts[0] === "stated_period_scores") {
    const index = Number(parts[1]);
    return `closing-period-${document.stated_period_scores[index]?.period ?? index + 1}`;
  }
  if (parts[0] === "final_score") return parts[1] === "ended_at" ? "closing-ended-at" : "closing-final-score";
  if (parts[0] === "recognition") return "closing-table-personnel";
  if (parts[0] === "officials") return "closing-officials";
  return "step-closing-top";
}

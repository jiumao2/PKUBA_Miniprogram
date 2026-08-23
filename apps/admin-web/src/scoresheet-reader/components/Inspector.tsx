import { AlertCircle, CheckCircle2, Clock3, History, Plus, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { pathToField } from '../lib/fieldPaths';
import { isValidJerseyNumber } from '../lib/jersey';
import {
  foulEditorOptions,
  foulOptionLabel,
  type FoulEditorOption,
} from '../lib/ruleProfiles';
import {
  insertScoreEvent,
  recalculateTeamEvents,
  removeScoreEvent,
  scoreTotalsByPeriod,
  semanticMark,
} from '../lib/score';
import type {
  DocumentChangeLogEntry,
  FoulCode,
  FoulEntry,
  OfficialEntry,
  PlayerEntry,
  RecognitionDiff,
  RecognitionRun,
  ScoreEvent,
  ScoresheetDocument,
  TeamEntry,
  TeamSide,
  ValidationIssue,
  ValidationReport,
} from '../types';
import { teamBySide } from '../types';
import { RecognitionPanel } from './RecognitionPanel';

interface InspectorProps {
  document: ScoresheetDocument;
  selectedField: string;
  validation: ValidationReport | null;
  changes: DocumentChangeLogEntry[];
  recognitionRun?: RecognitionRun | null;
  recognitionDiff?: RecognitionDiff | null;
  recognitionState?: 'idle' | 'starting' | 'running' | 'diff' | 'applied' | 'failed';
  onMutate: (mutation: (draft: ScoresheetDocument) => void) => void;
  onSelect: (field: string) => void;
  onApplyRecognition?: (regions: string[]) => Promise<void>;
  onDismissRecognitionDiff?: () => void;
  readOnly?: boolean;
}

const isLocked = (document: ScoresheetDocument, path: string) =>
  document.game_prior?.locked_paths.includes(path) ?? false;

const foulSuffixLabels = { '1': '₁', '2': '₂', '3': '₃' } as const;

function LabeledField({
  label,
  children,
  className = '',
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={`form-field ${className}`}>
      <span>{label}</span>
      {children}
    </label>
  );
}

function FoulSlot({
  slot,
  value,
  onChange,
  disabled = false,
  options,
  label = '犯规',
  autoFocus = false,
}: {
  slot: number;
  value?: FoulEntry;
  onChange: (entry?: FoulEntry) => void;
  disabled?: boolean;
  options: FoulEditorOption[];
  label?: string;
  autoFocus?: boolean;
}) {
  const selectedOption = options.find((option) => option.code === value?.code);
  const allowedSuffixes = selectedOption?.allowedSuffixes ?? [''];
  const freeThrowSuffixes = allowedSuffixes.filter(
    (suffix): suffix is '1' | '2' | '3' => suffix === '1' || suffix === '2' || suffix === '3',
  );
  const allowsCancellation = allowedSuffixes.includes('c');

  return (
    <div className={`foul-slot-editor${disabled ? ' is-disabled' : ''}`} aria-disabled={disabled}>
      <span className="slot-index">{slot}</span>
      <select
        aria-label={`${label} ${slot} 类型`}
        data-precise-focus={autoFocus || undefined}
        value={value?.code ?? ''}
        disabled={disabled}
        autoFocus={autoFocus}
        onChange={(event) => {
          if (!event.target.value) onChange(undefined);
          else {
            const code = event.target.value as FoulCode;
            const option = options.find((candidate) => candidate.code === code)!;
            const keepsFreeThrows = value?.free_throws != null
              && option.allowedSuffixes.includes(String(value.free_throws) as '1' | '2' | '3');
            const keepsCancellation = value?.cancelled && option.allowedSuffixes.includes('c');
            onChange({
              slot,
              code,
              catalog_id: option.catalogId,
              mark_style: option.markStyle,
              free_throws: keepsFreeThrows ? value?.free_throws ?? null : null,
              cancelled: Boolean(keepsCancellation),
              period: value?.period ?? null,
            });
          }
        }}
      >
        <option value="">未用</option>
        {options.map((option) => (
          <option key={`${option.markStyle}:${option.code}`} value={option.code}>{option.code}</option>
        ))}
      </select>
      <select
        aria-label={`${label} ${slot} 罚球下标`}
        value={value?.free_throws ?? ''}
        disabled={disabled || !value || value.cancelled || freeThrowSuffixes.length === 0}
        onChange={(event) =>
          value && onChange({ ...value, free_throws: event.target.value ? Number(event.target.value) : null, cancelled: false })
        }
      >
        <option value="">无下标</option>
        {freeThrowSuffixes.map((suffix) => (
          <option key={suffix} value={suffix}>{foulSuffixLabels[suffix]}</option>
        ))}
      </select>
      <select
        aria-label={`${label} ${slot} 节次`}
        value={value?.period ?? ''}
        disabled={disabled || !value}
        onChange={(event) => value && onChange({ ...value, period: event.target.value ? Number(event.target.value) : null })}
      >
        <option value="">节次未知</option>
        {[1, 2, 3, 4].map((period) => (
          <option key={period} value={period}>第 {period} 节</option>
        ))}
      </select>
      <label className="cancel-toggle">
        <input
          type="checkbox"
          checked={value?.cancelled ?? false}
          disabled={disabled || !value || value.free_throws != null || !allowsCancellation}
          onChange={(event) => value && onChange({ ...value, cancelled: event.target.checked, free_throws: null })}
        />
        c
      </label>
    </div>
  );
}

function HeaderEditor({
  document,
  onMutate,
  selectedField,
}: Pick<InspectorProps, 'document' | 'onMutate' | 'selectedField'>) {
  const teamA = teamBySide(document, 'A');
  const teamB = teamBySide(document, 'B');
  const headerFields: [keyof ScoresheetDocument['header'], string][] = [
    ['competition', '竞赛名称'],
    ['game_number', '比赛序号'],
    ['date', '日期'],
    ['scheduled_time', '计划时间'],
    ['venue', '地点'],
    ['crew_chief', '主裁判员'],
    ['umpire_1', '副裁判员 1'],
    ['umpire_2', '副裁判员 2'],
  ];
  return (
    <div className="inspector-section">
      <h3>比赛信息</h3>
      <div className="form-grid two-columns">
        <LabeledField label="A 队">
          <input
            data-precise-focus={selectedField === 'header.team_a_name' || undefined}
            autoFocus={selectedField === 'header.team_a_name'}
            value={teamA.name}
            disabled={isLocked(document, '/teams/0/name')}
            title={isLocked(document, '/teams/0/name') ? '由比赛信息锁定' : undefined}
            onChange={(event) => onMutate((draft) => { teamBySide(draft, 'A').name = event.target.value; })}
          />
        </LabeledField>
        <LabeledField label="B 队">
          <input
            data-precise-focus={selectedField === 'header.team_b_name' || undefined}
            autoFocus={selectedField === 'header.team_b_name'}
            value={teamB.name}
            disabled={isLocked(document, '/teams/1/name')}
            title={isLocked(document, '/teams/1/name') ? '由比赛信息锁定' : undefined}
            onChange={(event) => onMutate((draft) => { teamBySide(draft, 'B').name = event.target.value; })}
          />
        </LabeledField>
        {headerFields.map(([key, label]) => (
          <LabeledField label={label} key={key} className={key === 'competition' || key === 'venue' ? 'span-two' : ''}>
            <input
              type={key === 'date' ? 'date' : key === 'scheduled_time' ? 'time' : 'text'}
              step={key === 'scheduled_time' ? 60 : undefined}
              data-precise-focus={selectedField === `header.${key}` || undefined}
              autoFocus={selectedField === `header.${key}`}
              value={document.header[key]}
              disabled={isLocked(document, `/header/${key}`)}
              title={isLocked(document, `/header/${key}`) ? '由比赛信息锁定' : undefined}
              onChange={(event) => onMutate((draft) => { draft.header[key] = event.target.value; })}
            />
          </LabeledField>
        ))}
      </div>
    </div>
  );
}

function TeamEditor({
  document,
  side,
  onMutate,
  selectedField,
}: Pick<InspectorProps, 'document' | 'onMutate' | 'selectedField'> & { side: TeamSide }) {
  const team = teamBySide(document, side);
  const ruleProfile = document.rules_profile ?? 'fiba_2024';
  const coachFoulOptions = foulEditorOptions(ruleProfile, 'coach');
  const postFoulOptions = foulEditorOptions(ruleProfile, 'post_foul');
  const updateTeam = (mutation: (team: TeamEntry) => void) =>
    onMutate((draft) => mutation(teamBySide(draft, side)));

  return (
    <div className="inspector-section">
      <h3>{side} 队设置</h3>
      <div className="form-grid">
        <LabeledField label="队名">
          <input
            data-precise-focus={selectedField === `team.${side}.name` || undefined}
            autoFocus={selectedField === `team.${side}.name`}
            value={team.name}
            disabled={isLocked(document, `/teams/${side === 'A' ? 0 : 1}/name`)}
            title={isLocked(document, `/teams/${side === 'A' ? 0 : 1}/name`) ? '由比赛信息锁定' : undefined}
            onChange={(event) => updateTeam((draftTeam) => { draftTeam.name = event.target.value; })}
          />
        </LabeledField>
        <LabeledField label="教练员">
          <input data-precise-focus={selectedField === `team.${side}.head_coach` || undefined} autoFocus={selectedField === `team.${side}.head_coach`} value={team.head_coach} onChange={(event) => updateTeam((draftTeam) => { draftTeam.head_coach = event.target.value; })} />
        </LabeledField>
        <LabeledField label="助理教练员">
          <input data-precise-focus={selectedField === `team.${side}.assistant_coach` || undefined} autoFocus={selectedField === `team.${side}.assistant_coach`} value={team.assistant_coach} onChange={(event) => updateTeam((draftTeam) => { draftTeam.assistant_coach = event.target.value; })} />
        </LabeledField>
      </div>

      <div className="subsection-heading"><span>暂停</span><small>输入比赛分钟，留空表示未使用</small></div>
      {(['H1', 'H2', 'OT'] as const).map((scope) => {
        const slotCount = scope === 'H1' ? 2 : 3;
        return (
          <div className="compact-row" key={scope}>
            <span className="row-label">{scope}</span>
            {Array.from({ length: slotCount }, (_, index) => index + 1).map((slot) => {
              const timeout = team.timeouts.find((entry) => entry.scope === scope && entry.slot === slot);
              return (
                <input
                  key={slot}
                  aria-label={`${side} 队 ${scope} 暂停 ${slot}`}
                  type="number"
                  min="0"
                  max="10"
                  data-precise-focus={selectedField === `team.${side}.timeout.${scope}.${slot}` || undefined}
                  autoFocus={selectedField === `team.${side}.timeout.${scope}.${slot}`}
                  value={timeout?.minute ?? ''}
                  onChange={(event) =>
                    updateTeam((draftTeam) => {
                      draftTeam.timeouts = draftTeam.timeouts.filter((entry) => !(entry.scope === scope && entry.slot === slot));
                      if (event.target.value !== '') {
                        draftTeam.timeouts.push({ scope, slot, minute: Number(event.target.value) });
                      }
                    })
                  }
                />
              );
            })}
          </div>
        );
      })}

      <div className="subsection-heading"><span>全队犯规</span><small>0–4 格</small></div>
      <div className="team-foul-controls">
        {[1, 2, 3, 4].map((period) => {
          const value = team.team_fouls.find((entry) => entry.period === period)?.count ?? 0;
          return (
            <LabeledField label={`第 ${period} 节`} key={period}>
              <input
                type="number"
                min="0"
                max="4"
                data-precise-focus={selectedField.startsWith(`team.${side}.team_foul.${period}.`) || undefined}
                autoFocus={selectedField.startsWith(`team.${side}.team_foul.${period}.`)}
                value={value}
                onChange={(event) =>
                  updateTeam((draftTeam) => {
                    const next = Math.max(0, Math.min(4, Number(event.target.value)));
                    const existing = draftTeam.team_fouls.find((entry) => entry.period === period);
                    if (existing) existing.count = next;
                    else draftTeam.team_fouls.push({ period, count: next });
                  })
                }
              />
            </LabeledField>
          );
        })}
      </div>

      <div className="subsection-heading"><span>教练员犯规</span><small>记录表只有 3 个正式格</small></div>
      {Array.from({ length: 3 }, (_, index) => index + 1).map((slot) => {
        const disabled = slot > 1 && !(team.coach_fouls ?? []).some((entry) => entry.slot === slot - 1);
        return (
          <FoulSlot
            key={slot}
            slot={slot}
            options={coachFoulOptions}
            disabled={disabled}
            autoFocus={selectedField === `team.${side}.coach_foul.${slot}`}
            value={(team.coach_fouls ?? []).find((entry) => entry.slot === slot)}
            onChange={(entry) =>
              updateTeam((draftTeam) => {
                draftTeam.coach_fouls ??= [];
                draftTeam.coach_post_foul_markers ??= [];
                if (!entry) {
                  draftTeam.coach_fouls = draftTeam.coach_fouls.filter((foul) => foul.slot < slot);
                  draftTeam.coach_post_foul_markers = [];
                } else {
                  draftTeam.coach_fouls = draftTeam.coach_fouls.filter((foul) => foul.slot !== slot);
                  draftTeam.coach_fouls.push(entry);
                  draftTeam.coach_fouls.sort((a, b) => a.slot - b.slot);
                }
              })
            }
          />
        );
      })}
      <div className="subsection-heading compact"><span>第 3 格后附加标记</span><small>{foulOptionLabel(postFoulOptions)}，不计作第 4 次犯规</small></div>
      {Array.from({ length: 2 }, (_, index) => index + 1).map((slot) => {
        const markers = team.coach_post_foul_markers ?? [];
        const disabled = !(team.coach_fouls ?? []).some((entry) => entry.slot === 3)
          || (slot > 1 && !markers.some((entry) => entry.slot === slot - 1));
        return (
          <FoulSlot
            key={`post-${slot}`}
            slot={slot}
            label="附加标记"
            options={postFoulOptions}
            disabled={disabled}
            autoFocus={selectedField === `team.${side}.coach_post_foul`}
            value={markers.find((entry) => entry.slot === slot)}
            onChange={(entry) => updateTeam((draftTeam) => {
              draftTeam.coach_post_foul_markers ??= [];
              if (!entry) draftTeam.coach_post_foul_markers = draftTeam.coach_post_foul_markers.filter((marker) => marker.slot < slot);
              else {
                draftTeam.coach_post_foul_markers = draftTeam.coach_post_foul_markers.filter((marker) => marker.slot !== slot);
                draftTeam.coach_post_foul_markers.push(entry);
                draftTeam.coach_post_foul_markers.sort((a, b) => a.slot - b.slot);
              }
            })}
          />
        );
      })}

      <div className="subsection-heading"><span>助理教练员行犯规</span><small>接任主教练后使用相同类型</small></div>
      <p className="section-note">接任前的席位技术犯规以 B 记在主教练行；接任后可在本人行填写 {foulOptionLabel(coachFoulOptions)}。</p>
      {Array.from({ length: 3 }, (_, index) => index + 1).map((slot) => {
        const assistantFouls = team.assistant_coach_fouls ?? [];
        const disabled = slot > 1 && !assistantFouls.some((entry) => entry.slot === slot - 1);
        return (
          <FoulSlot
            key={`assistant-${slot}`}
            slot={slot}
            label="助理教练员犯规"
            options={coachFoulOptions}
            disabled={disabled}
            autoFocus={selectedField === `team.${side}.assistant_coach_foul.${slot}`}
            value={assistantFouls.find((entry) => entry.slot === slot)}
            onChange={(entry) => updateTeam((draftTeam) => {
              draftTeam.assistant_coach_fouls ??= [];
              draftTeam.assistant_coach_post_foul_markers ??= [];
              if (!entry) {
                draftTeam.assistant_coach_fouls = draftTeam.assistant_coach_fouls.filter((foul) => foul.slot < slot);
                draftTeam.assistant_coach_post_foul_markers = [];
                return;
              }
              draftTeam.assistant_coach_fouls = draftTeam.assistant_coach_fouls.filter((foul) => foul.slot !== slot);
              draftTeam.assistant_coach_fouls.push(entry);
              draftTeam.assistant_coach_fouls.sort((a, b) => a.slot - b.slot);
            })}
          />
        );
      })}
      <div className="subsection-heading compact"><span>助理教练员第 3 格后附加标记</span><small>{foulOptionLabel(postFoulOptions)}</small></div>
      {Array.from({ length: 2 }, (_, index) => index + 1).map((slot) => {
        const markers = team.assistant_coach_post_foul_markers ?? [];
        const disabled = !(team.assistant_coach_fouls ?? []).some((entry) => entry.slot === 3)
          || (slot > 1 && !markers.some((entry) => entry.slot === slot - 1));
        return (
          <FoulSlot
            key={`assistant-post-${slot}`}
            slot={slot}
            label="助理教练员附加标记"
            options={postFoulOptions}
            disabled={disabled}
            autoFocus={selectedField === `team.${side}.assistant_coach_post_foul`}
            value={markers.find((entry) => entry.slot === slot)}
            onChange={(entry) => updateTeam((draftTeam) => {
              draftTeam.assistant_coach_post_foul_markers ??= [];
              if (!entry) draftTeam.assistant_coach_post_foul_markers = draftTeam.assistant_coach_post_foul_markers.filter((marker) => marker.slot < slot);
              else {
                draftTeam.assistant_coach_post_foul_markers = draftTeam.assistant_coach_post_foul_markers.filter((marker) => marker.slot !== slot);
                draftTeam.assistant_coach_post_foul_markers.push(entry);
                draftTeam.assistant_coach_post_foul_markers.sort((a, b) => a.slot - b.slot);
              }
            })}
          />
        );
      })}
    </div>
  );
}

function emptyPlayer(row: number): PlayerEntry {
  return {
    row,
    license_number: '',
    name: '',
    jersey_number: '',
    captain: false,
    participation: 'none',
    fouls: [],
    post_foul_markers: [],
  };
}

function PlayerEditor({
  document,
  side,
  row,
  onMutate,
  selectedField,
}: Pick<InspectorProps, 'document' | 'onMutate' | 'selectedField'> & { side: TeamSide; row: number }) {
  const team = teamBySide(document, side);
  const player = team.players.find((entry) => entry.row === row) ?? emptyPlayer(row);
  const ruleProfile = document.rules_profile ?? 'fiba_2024';
  const playerFoulOptions = foulEditorOptions(ruleProfile, 'player');
  const postFoulOptions = foulEditorOptions(ruleProfile, 'post_foul');
  const priorNames = side === 'A'
    ? document.game_prior?.team_a.player_names
    : document.game_prior?.team_b.player_names;
  const jerseyIsValid = isValidJerseyNumber(player.jersey_number);
  const updatePlayer = (mutation: (player: PlayerEntry) => void) =>
    onMutate((draft) => {
      const draftTeam = teamBySide(draft, side);
      let draftPlayer = draftTeam.players.find((entry) => entry.row === row);
      if (!draftPlayer) {
        draftPlayer = emptyPlayer(row);
        draftTeam.players.push(draftPlayer);
        draftTeam.players.sort((a, b) => a.row - b.row);
      }
      mutation(draftPlayer);
    });

  return (
    <div className="inspector-section">
      <div className="editor-title-row">
        <div>
          <span className="pane-kicker">{side} 队</span>
          <h3>第 {row} 行队员</h3>
        </div>
        {team.players.some((entry) => entry.row === row) ? (
          <button
            className="destructive-icon"
            title="清空该行"
            onClick={() => onMutate((draft) => {
              const draftTeam = teamBySide(draft, side);
              draftTeam.players = draftTeam.players.filter((entry) => entry.row !== row);
            })}
          >
            <Trash2 size={16} />
          </button>
        ) : null}
      </div>
      <div className="form-grid two-columns">
        <LabeledField label="证件号码">
          <input data-precise-focus={selectedField.endsWith('.license') || undefined} autoFocus={selectedField.endsWith('.license')} value={player.license_number} onChange={(event) => updatePlayer((draftPlayer) => { draftPlayer.license_number = event.target.value; })} />
        </LabeledField>
        <LabeledField label="球衣号码">
          <input
            inputMode="numeric"
            data-precise-focus={selectedField.endsWith('.jersey') || undefined}
            autoFocus={selectedField.endsWith('.jersey')}
            value={player.jersey_number}
            aria-invalid={!jerseyIsValid}
            onChange={(event) => updatePlayer((draftPlayer) => {
              draftPlayer.jersey_number = event.target.value.replace(/[^0-9]/g, '').slice(0, 2);
            })}
          />
          <small className={jerseyIsValid ? 'field-help' : 'field-error'}>
            {jerseyIsValid ? '允许 0、00、1–99' : '号码格式无效'}
          </small>
        </LabeledField>
        <LabeledField label="姓名" className="span-two">
          {priorNames ? (
            <select
              data-precise-focus={selectedField.endsWith('.name') || undefined}
              autoFocus={selectedField.endsWith('.name')}
              value={player.name}
              onChange={(event) => updatePlayer((draftPlayer) => { draftPlayer.name = event.target.value; })}
            >
              <option value="">未确认</option>
              {player.name && !priorNames.includes(player.name) ? <option value={player.name}>{player.name}（当前值）</option> : null}
              {priorNames.map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          ) : (
            <input data-precise-focus={selectedField.endsWith('.name') || undefined} autoFocus={selectedField.endsWith('.name')} value={player.name} onChange={(event) => updatePlayer((draftPlayer) => { draftPlayer.name = event.target.value; })} />
          )}
        </LabeledField>
        <LabeledField label="上场状态">
          <select data-precise-focus={selectedField.endsWith('.participation') || undefined} autoFocus={selectedField.endsWith('.participation')} value={player.participation} onChange={(event) => updatePlayer((draftPlayer) => { draftPlayer.participation = event.target.value as PlayerEntry['participation']; })}>
            <option value="none">未上场</option>
            <option value="starter">首发（圈 x）</option>
            <option value="substitute">替补（x）</option>
          </select>
        </LabeledField>
        <label className="checkbox-field">
          <input type="checkbox" checked={player.captain} onChange={(event) => updatePlayer((draftPlayer) => { draftPlayer.captain = event.target.checked; })} />
          队长（CAP）
        </label>
      </div>
      <div className="subsection-heading"><span>个人犯规</span><small>字母、罚球下标、节次、抵消 c</small></div>
      {Array.from({ length: 5 }, (_, index) => index + 1).map((slot) => {
        const disabled = slot > 1 && !player.fouls.some((foul) => foul.slot === slot - 1);
        return (
          <FoulSlot
            key={slot}
            slot={slot}
            options={playerFoulOptions}
            disabled={disabled}
            autoFocus={selectedField.endsWith(`.foul.${slot}`)}
            value={player.fouls.find((foul) => foul.slot === slot)}
            onChange={(entry) =>
              updatePlayer((draftPlayer) => {
                draftPlayer.post_foul_markers ??= [];
                if (!entry) {
                  draftPlayer.fouls = draftPlayer.fouls.filter((foul) => foul.slot < slot);
                  draftPlayer.post_foul_markers = [];
                } else {
                  draftPlayer.fouls = draftPlayer.fouls.filter((foul) => foul.slot !== slot);
                  draftPlayer.fouls.push(entry);
                  draftPlayer.fouls.sort((a, b) => a.slot - b.slot);
                }
              })
            }
          />
        );
      })}
      <div className="subsection-heading compact"><span>第 5 格后附加标记</span><small>{foulOptionLabel(postFoulOptions)}，使用假想列渲染</small></div>
      {Array.from({ length: 2 }, (_, index) => index + 1).map((slot) => {
        const markers = player.post_foul_markers ?? [];
        const disabled = !player.fouls.some((foul) => foul.slot === 5)
          || (slot > 1 && !markers.some((marker) => marker.slot === slot - 1));
        return (
          <FoulSlot
            key={`post-${slot}`}
            slot={slot}
            label="附加标记"
            options={postFoulOptions}
            disabled={disabled}
            autoFocus={selectedField.endsWith('.post_foul')}
            value={markers.find((marker) => marker.slot === slot)}
            onChange={(entry) => updatePlayer((draftPlayer) => {
              draftPlayer.post_foul_markers ??= [];
              if (!entry) draftPlayer.post_foul_markers = draftPlayer.post_foul_markers.filter((marker) => marker.slot < slot);
              else {
                draftPlayer.post_foul_markers = draftPlayer.post_foul_markers.filter((marker) => marker.slot !== slot);
                draftPlayer.post_foul_markers.push(entry);
                draftPlayer.post_foul_markers.sort((a, b) => a.slot - b.slot);
              }
            })}
          />
        );
      })}
    </div>
  );
}

function ScoreEditor({
  document,
  side,
  cumulative,
  selectedField,
  onMutate,
  onSelect,
}: Pick<InspectorProps, 'document' | 'selectedField' | 'onMutate' | 'onSelect'> & { side: TeamSide; cumulative: number }) {
  const event = document.score_events.find(
    (entry) => entry.team === side && entry.cumulative_score === cumulative,
  );
  const team = teamBySide(document, side);
  const events = document.score_events
    .filter((entry) => entry.team === side)
    .sort((a, b) => a.sequence - b.sequence);
  const periodTotals = scoreTotalsByPeriod(document, side);
  const periods = [1, 2, 3, 4, 5];
  const inferredSelectedPeriod = event?.period
    ?? events.find((entry) => entry.cumulative_score > cumulative)?.period
    ?? [...events].reverse().find((entry) => entry.cumulative_score < cumulative)?.period
    ?? 1;
  const [activePeriod, setActivePeriod] = useState(inferredSelectedPeriod);
  const eventRows = useRef(new Map<number, HTMLDivElement>());
  const periodEvents = events.filter((entry) => entry.period === activePeriod);
  const activeDerived = periodTotals.get(activePeriod) ?? 0;
  const activeStated = document.stated_period_scores.find((entry) => entry.period === activePeriod);
  const activePaper = activeStated?.[side === 'A' ? 'team_a' : 'team_b'];
  const activeMatches = activePaper != null && activePaper === activeDerived;
  const previous = Math.max(
    0,
    ...events
      .filter((entry) => entry.cumulative_score < cumulative)
      .map((entry) => entry.cumulative_score),
  );
  const inferredPoints = cumulative - previous;
  const firstJersey = team.players.find((player) => player.jersey_number)?.jersey_number ?? '';
  const scoreField = (value: number) => `score.${side}.${String(value).padStart(3, '0')}`;
  const periodLabel = (period: number) => period <= 4 ? `Q${period}` : 'OT';

  useEffect(() => {
    setActivePeriod(inferredSelectedPeriod);
  }, [side, cumulative, inferredSelectedPeriod]);

  useEffect(() => {
    if (!event || activePeriod !== event.period) return;
    eventRows.current.get(event.sequence)?.scrollIntoView?.({
      behavior: selectedField.endsWith('.edit') ? 'smooth' : 'auto',
      block: 'nearest',
    });
  }, [activePeriod, event, selectedField]);

  const updateEvent = (sequence: number, mutation: (event: ScoreEvent) => void) => {
    let nextField = '';
    let nextPeriod = activePeriod;
    onMutate((draft) => {
      const draftEvent = draft.score_events.find((entry) => entry.sequence === sequence);
      if (!draftEvent) return;
      mutation(draftEvent);
      recalculateTeamEvents(draft, side);
      nextField = scoreField(draftEvent.cumulative_score);
      nextPeriod = draftEvent.period;
    });
    setActivePeriod(nextPeriod);
    if (nextField) onSelect(nextField);
  };

  const addRelative = (
    anchor: ScoreEvent | undefined,
    position: 'before' | 'after' | 'end',
    periodOverride?: number,
  ) => {
    let nextField = '';
    let nextPeriod = periodOverride ?? anchor?.period ?? events.at(-1)?.period ?? 1;
    onMutate((draft) => {
      const draftAnchor = anchor
        ? draft.score_events.find((entry) => entry.sequence === anchor.sequence)
        : undefined;
      nextPeriod = periodOverride ?? draftAnchor?.period ?? events.at(-1)?.period ?? 1;
      const inserted = insertScoreEvent(
        draft,
        side,
        draftAnchor?.sequence ?? null,
        position,
        {
          period: nextPeriod,
          points: 2,
          scorerJersey: draftAnchor?.scorer_jersey || firstJersey,
        },
      );
      nextField = scoreField(inserted.cumulative_score);
    });
    setActivePeriod(nextPeriod);
    if (nextField) onSelect(nextField);
  };

  const addInActivePeriod = () => {
    const lastInPeriod = periodEvents.at(-1);
    if (lastInPeriod) {
      addRelative(lastInPeriod, 'after', activePeriod);
      return;
    }
    const firstLater = events.find((entry) => entry.period > activePeriod);
    addRelative(firstLater ?? events.at(-1), firstLater ? 'before' : 'end', activePeriod);
  };

  const addAtSelectedCell = () => {
    const next = events.find((entry) => entry.cumulative_score > cumulative);
    const prior = [...events].reverse().find((entry) => entry.cumulative_score < cumulative);
    let nextField = '';
    onMutate((draft) => {
      const draftNext = next
        ? draft.score_events.find((entry) => entry.sequence === next.sequence)
        : undefined;
      const inserted = insertScoreEvent(
        draft,
        side,
        draftNext?.sequence ?? null,
        draftNext ? 'before' : 'end',
        {
          period: draftNext?.period ?? prior?.period ?? 1,
          points: inferredPoints,
          scorerJersey: firstJersey,
        },
      );
      nextField = scoreField(inserted.cumulative_score);
    });
    if (nextField) onSelect(nextField);
  };

  return (
    <div className="inspector-section score-editor">
      <span className="pane-kicker">累计分</span>
      <h3>{side} 队 · {cumulative} 分格</h3>
      <div className="score-reconciliation" aria-label={`${side} 队节比分核对`}>
        <div className="score-reconciliation-heading">
          <span>按节查看</span><small>running score / 纸面节分</small>
        </div>
        <div className="score-period-tabs" role="tablist" aria-label={`${side} 队得分节次`}>
          {periods.map((period) => {
            const derived = periodTotals.get(period) ?? 0;
            const stated = document.stated_period_scores.find((entry) => entry.period === period);
            const paper = stated?.[side === 'A' ? 'team_a' : 'team_b'];
            const matches = paper != null && paper === derived;
            return (
              <button
                key={period}
                role="tab"
                aria-selected={activePeriod === period}
                className={`${paper == null ? 'is-missing' : matches ? 'is-match' : 'is-mismatch'}${activePeriod === period ? ' is-active' : ''}`}
                onClick={() => setActivePeriod(period)}
                title={`${periodLabel(period)}：逐次得分 ${derived}，纸面 ${paper ?? '未填写'}`}
              >
                <span>{periodLabel(period)}</span>
                <span className="score-period-tab-total"><strong>{derived}</strong><i>/</i><b>{paper ?? '—'}</b></span>
              </button>
            );
          })}
        </div>
        <div className={`score-period-status${activePaper == null ? ' is-missing' : activeMatches ? ' is-match' : ' is-mismatch'}`}>
          <strong>{periodLabel(activePeriod)}</strong>
          <span>{periodEvents.length} 次得分事件</span>
          <b>{activeDerived} / {activePaper ?? '未填'}</b>
        </div>
      </div>

      {!event ? (
        <div className="score-empty-cell">
          <div>
            <strong>{cumulative} 分格尚无事件</strong>
            <span>前一项为 {previous} 分</span>
          </div>
          {inferredPoints >= 1 && inferredPoints <= 3 && firstJersey ? (
            <button className="primary-action" onClick={addAtSelectedCell}>
              <Plus size={14} /> 在此补录 {inferredPoints} 分
            </button>
          ) : (
            <small>需要先补齐前面缺失的得分，使相邻增量为 1、2 或 3。</small>
          )}
        </div>
      ) : null}

      <div className="score-ledger-heading">
        <div><strong>{periodLabel(activePeriod)} 得分事件</strong><span>本节 {periodEvents.length} 笔 · 全场 {events.length} 笔</span></div>
        <button className="compact-action" onClick={addInActivePeriod} disabled={!firstJersey}>
          <Plus size={13} /> 本节新增
        </button>
      </div>
      <div className="score-ledger" role="tabpanel" aria-label={`${side} 队得分事件账本`}>
        {periodEvents.map((entry) => {
          const selected = entry.sequence === event?.sequence;
          const rosterHasJersey = team.players.some((player) => player.jersey_number === entry.scorer_jersey);
          const unresolvedPoints = entry.points == null;
          const invalidPoints = entry.points != null && ![1, 2, 3].includes(entry.points);
          return (
            <div
              key={entry.sequence}
              ref={(node) => {
                if (node) eventRows.current.set(entry.sequence, node);
                else eventRows.current.delete(entry.sequence);
              }}
              data-score-field={scoreField(entry.cumulative_score)}
              data-score-period={entry.period}
              className={`score-ledger-row${selected ? ' is-selected' : ''}${selected && selectedField.endsWith('.edit') ? ' is-targeted' : ''}${unresolvedPoints ? ' is-unresolved' : ''}${invalidPoints ? ' is-invalid-points' : ''}`}
            >
              <button
                className="score-ledger-total"
                aria-label={`选择${side}队累计 ${entry.cumulative_score} 分事件`}
                onClick={() => onSelect(scoreField(entry.cumulative_score))}
              >
                <strong>{entry.cumulative_score}</strong><small>累计</small>
              </button>
              <label>
                <span>队员</span>
                <select
                  aria-label={selected ? '得分队员' : `${side}队累计${entry.cumulative_score}分 得分队员`}
                  value={entry.scorer_jersey}
                  onChange={(change) => updateEvent(entry.sequence, (draftEvent) => { draftEvent.scorer_jersey = change.target.value; })}
                >
                  {!rosterHasJersey && entry.scorer_jersey ? <option value={entry.scorer_jersey}>{entry.scorer_jersey} · 未在名单</option> : null}
                  {team.players.filter((player) => player.jersey_number).map((player) => (
                    <option key={player.row} value={player.jersey_number}>{player.jersey_number} · {player.name}</option>
                  ))}
                </select>
              </label>
              <label>
                <span>得分</span>
                <select
                  aria-label={selected ? '本次得分' : `${side}队累计${entry.cumulative_score}分 本次得分`}
                  value={entry.points ?? ''}
                  onChange={(change) => updateEvent(entry.sequence, (draftEvent) => {
                    draftEvent.points = Number(change.target.value);
                    Object.assign(draftEvent, semanticMark(draftEvent.points));
                  })}
                >
                  {unresolvedPoints ? <option value="" disabled>待确定</option> : null}
                  {invalidPoints ? <option value={entry.points ?? ''} disabled>异常值 {entry.points}</option> : null}
                  <option value="1">1 · ●</option>
                  <option value="2">2 · ╱</option>
                  <option value="3">3 · ╱○</option>
                </select>
              </label>
              <label>
                <span>节次</span>
                <select
                  aria-label={selected ? '节次' : `${side}队累计${entry.cumulative_score}分 节次`}
                  value={entry.period}
                  onChange={(change) => updateEvent(entry.sequence, (draftEvent) => { draftEvent.period = Number(change.target.value); })}
                >
                  {[1, 2, 3, 4, 5].map((period) => <option key={period} value={period}>{periodLabel(period)}</option>)}
                </select>
              </label>
              <label className="score-ledger-boundary">
                <span>节末</span>
                <input
                  type="checkbox"
                  aria-label={selected ? '节末标记' : `${side}队累计${entry.cumulative_score}分 节末标记`}
                  checked={entry.boundary === 'period_end' || entry.boundary === 'game_end'}
                  onChange={(change) => updateEvent(entry.sequence, (draftEvent) => { draftEvent.boundary = change.target.checked ? 'period_end' : 'none'; })}
                />
              </label>
              <div className="score-ledger-actions">
                <button aria-label={`在累计 ${entry.cumulative_score} 分之前插入`} title="之前插入" onClick={() => addRelative(entry, 'before')}><Plus size={12} />前</button>
                <button aria-label={`在累计 ${entry.cumulative_score} 分之后插入`} title="之后插入" onClick={() => addRelative(entry, 'after')}><Plus size={12} />后</button>
                <button
                  className="danger"
                  aria-label={`删除累计 ${entry.cumulative_score} 分事件`}
                  title="删除"
                  onClick={() => {
                    onMutate((draft) => removeScoreEvent(draft, entry.sequence));
                    const fallback = events.find((candidate) => candidate.sequence !== entry.sequence);
                    onSelect(fallback ? scoreField(fallback.cumulative_score) : `team.${side}.score`);
                  }}
                ><Trash2 size={12} /></button>
              </div>
            </div>
          );
        })}
        {periodEvents.length === 0 ? <p className="section-note score-period-empty">{periodLabel(activePeriod)} 尚无得分事件，可在本节新增；纸面为 0 分时保持为空即表示核对一致。</p> : null}
      </div>
      <p className="section-note score-boundary-note">纸面节比分保持独立，不会被事件合计覆盖。双方最终分与最后累计分一致时，比赛结束双横线自动生成。</p>
    </div>
  );
}

function SummaryEditor({
  document,
  onMutate,
  selectedField,
}: Pick<InspectorProps, 'document' | 'onMutate' | 'selectedField'>) {
  const deriveResult = (draft: ScoresheetDocument) => {
    const teamA = draft.stated_period_scores.reduce((total, row) => total + row.team_a, 0);
    const teamB = draft.stated_period_scores.reduce((total, row) => total + row.team_b, 0);
    const nameA = draft.game_prior?.team_a.name ?? teamBySide(draft, 'A').name;
    const nameB = draft.game_prior?.team_b.name ?? teamBySide(draft, 'B').name;
    draft.final_score.team_a = teamA;
    draft.final_score.team_b = teamB;
    draft.final_score.winner_name = teamA > teamB ? nameA : teamB > teamA ? nameB : '';
  };
  return (
    <div className="inspector-section">
      <h3>书面汇总</h3>
      <p className="section-note">各节比分按纸面填写；最终比分与胜队由各节比分自动计算。</p>
      <div className="period-score-grid">
        <span /> <b>A</b> <b>B</b>
        {[1, 2, 3, 4, 5].map((period) => {
          const score = document.stated_period_scores.find((entry) => entry.period === period);
          const update = (side: 'team_a' | 'team_b', value: number) =>
            onMutate((draft) => {
              let draftScore = draft.stated_period_scores.find((entry) => entry.period === period);
              if (!draftScore) {
                draftScore = { period, team_a: 0, team_b: 0 };
                draft.stated_period_scores.push(draftScore);
                draft.stated_period_scores.sort((a, b) => a.period - b.period);
              }
              draftScore[side] = value;
              deriveResult(draft);
            });
          return (
            <div className="period-score-row" key={period}>
              <span>{period === 5 ? '决胜期' : `第 ${period} 节`}</span>
              <input
                aria-label={`${period === 5 ? '决胜期' : `第 ${period} 节`} A 队得分`}
                data-precise-focus={selectedField === `summary.period.${period}.A` || undefined}
                autoFocus={selectedField === `summary.period.${period}.A`}
                type="number"
                min="0"
                value={score?.team_a ?? 0}
                onChange={(event) => update('team_a', Number(event.target.value))}
              />
              <input
                aria-label={`${period === 5 ? '决胜期' : `第 ${period} 节`} B 队得分`}
                data-precise-focus={selectedField === `summary.period.${period}.B` || undefined}
                autoFocus={selectedField === `summary.period.${period}.B`}
                type="number"
                min="0"
                value={score?.team_b ?? 0}
                onChange={(event) => update('team_b', Number(event.target.value))}
              />
            </div>
          );
        })}
      </div>
      <div className="form-grid two-columns summary-form">
        <LabeledField label="A 队最终比分">
          <input aria-label="A 队最终比分" data-precise-focus={selectedField === 'summary.final.A' || undefined} autoFocus={selectedField === 'summary.final.A'} type="number" value={document.final_score.team_a} readOnly />
        </LabeledField>
        <LabeledField label="B 队最终比分">
          <input aria-label="B 队最终比分" data-precise-focus={selectedField === 'summary.final.B' || undefined} autoFocus={selectedField === 'summary.final.B'} type="number" value={document.final_score.team_b} readOnly />
        </LabeledField>
        <LabeledField label="胜队" className="span-two">
          <input
            aria-label="胜队"
            data-precise-focus={selectedField === 'summary.winner' || undefined}
            autoFocus={selectedField === 'summary.winner'}
            value={document.final_score.winner_name}
            placeholder="比分相同时不产生胜队"
            readOnly
          />
        </LabeledField>
        <LabeledField label="比赛结束时间" className="span-two">
          <input data-precise-focus={selectedField === 'summary.ended_at' || undefined} autoFocus={selectedField === 'summary.ended_at'} type="time" step="60" value={document.final_score.ended_at} onChange={(event) => onMutate((draft) => { draft.final_score.ended_at = event.target.value; })} />
        </LabeledField>
      </div>
    </div>
  );
}

const officialLabels: Record<OfficialEntry['role'], string> = {
  scorer: '记录员',
  assistant_scorer: '助理记录员',
  timer: '计时员',
  shot_clock_operator: '24 秒计时员',
  crew_chief: '主裁判员',
  umpire_1: '副裁判员 1',
  umpire_2: '副裁判员 2',
  protest_captain: '申诉队长',
};

function OfficialsEditor({
  document,
  onMutate,
  selectedField,
}: Pick<InspectorProps, 'document' | 'onMutate' | 'selectedField'>) {
  const tablePersonnel = document.recognition?.table_personnel ?? [];
  return (
    <div className="inspector-section">
      <h3>工作人员</h3>
      <p className="section-note">模型只识别记录台人员姓名，不根据纸面位置猜测岗位。</p>
      {document.recognition ? (
        <>
          <div className="subsection-heading compact table-personnel-heading">
            <span>识别到的记录台人员</span>
            <small>不分岗位</small>
          </div>
          <div className="table-personnel-list">
            {tablePersonnel.map((name, index) => (
              <div className="table-personnel-row" key={index}>
                <span>{index + 1}</span>
                <input
                  aria-label={`记录台人员 ${index + 1}`}
                  value={name}
                  onChange={(event) => onMutate((draft) => {
                    if (draft.recognition) {
                      draft.recognition.table_personnel ??= [];
                      draft.recognition.table_personnel[index] = event.target.value;
                    }
                  })}
                />
                <button
                  type="button"
                  className="destructive-icon"
                  aria-label={`删除记录台人员 ${index + 1}`}
                  onClick={() => onMutate((draft) => {
                    draft.recognition?.table_personnel?.splice(index, 1);
                  })}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            {tablePersonnel.length === 0 ? (
              <p className="table-personnel-empty">模型没有辨认出记录台人员，可在此人工补充。</p>
            ) : null}
            <button
              type="button"
              className="secondary-action table-personnel-add"
              onClick={() => onMutate((draft) => {
                if (draft.recognition) {
                  draft.recognition.table_personnel ??= [];
                  draft.recognition.table_personnel.push('');
                }
              })}
            >
              <Plus size={13} />添加人员
            </button>
          </div>
        </>
      ) : null}
      <div className="subsection-heading compact optional-role-heading">
        <span>纸面岗位填写</span>
        <small>人工可选</small>
      </div>
      <p className="field-help role-assignment-help">
        以下岗位不会由模型自动猜测；没有填写或看不清时保持为空。
      </p>
      <div className="official-list">
        {document.officials.map((official) => (
          <div className="official-row" key={official.role}>
            <span>{officialLabels[official.role]}</span>
            <input aria-label={`${officialLabels[official.role]}姓名`} data-precise-focus={selectedField === `official.${official.role}.name` || undefined} autoFocus={selectedField === `official.${official.role}.name`} value={official.name} onChange={(event) => onMutate((draft) => { const target = draft.officials.find((entry) => entry.role === official.role)!; target.name = event.target.value; })} />
          </div>
        ))}
      </div>
    </div>
  );
}

function selectionLabel(field: string): string {
  const headerField = field.match(/^header\.(.+)$/);
  if (headerField) {
    const labels: Record<string, string> = {
      team_a_name: 'A 队名称', team_b_name: 'B 队名称', competition: '竞赛名称',
      game_number: '比赛序号', date: '日期', scheduled_time: '计划时间', venue: '地点',
      crew_chief: '主裁判员', umpire_1: '副裁判员 1', umpire_2: '副裁判员 2',
    };
    return `比赛信息 · ${labels[headerField[1]] ?? headerField[1]}`;
  }
  const teamFoul = field.match(/^team\.(A|B)\.team_foul\.(\d+)\.(\d+)$/);
  if (teamFoul) return `${teamFoul[1]} 队 · 第 ${teamFoul[2]} 节全队犯规 · 第 ${teamFoul[3]} 格`;
  const timeout = field.match(/^team\.(A|B)\.timeout\.(H1|H2|OT)\.(\d+)$/);
  if (timeout) return `${timeout[1]} 队 · ${timeout[2]} 暂停 · 第 ${timeout[3]} 格`;
  const player = field.match(/^team\.(A|B)\.player\.(\d{2})(?:\.(.+))?$/);
  if (player) {
    const suffix: Record<string, string> = {
      license: '证件号码', name: '姓名', jersey: '球衣号码', participation: '上场状态', post_foul: '附加标记',
    };
    const detail = player[3]?.startsWith('foul.') ? `第 ${player[3].split('.')[1]} 个犯规` : suffix[player[3] ?? ''] ?? '';
    return `${player[1]} 队 · 第 ${Number(player[2])} 行队员${detail ? ` · ${detail}` : ''}`;
  }
  const coachFoul = field.match(/^team\.(A|B)\.coach_foul\.(\d+)$/);
  if (coachFoul) return `${coachFoul[1]} 队 · 教练第 ${coachFoul[2]} 个犯规`;
  const assistantCoachFoul = field.match(/^team\.(A|B)\.assistant_coach_foul\.(\d+)$/);
  if (assistantCoachFoul) return `${assistantCoachFoul[1]} 队 · 助理教练员第 ${assistantCoachFoul[2]} 个犯规`;
  const assistantCoachPost = field.match(/^team\.(A|B)\.assistant_coach_post_foul$/);
  if (assistantCoachPost) return `${assistantCoachPost[1]} 队 · 助理教练员附加标记`;
  const team = field.match(/^team\.(A|B)\.(.+)$/);
  if (team) return `${team[1]} 队 · ${team[2] === 'name' ? '队名' : team[2].includes('coach') ? '教练员' : '信息区'}`;
  const score = field.match(/^score\.(A|B)\.(\d{3})/);
  if (score) return `${score[1]} 队 · 累计 ${Number(score[2])} 分格`;
  const periodScore = field.match(/^summary\.period\.(\d+)\.(A|B)$/);
  if (periodScore) return `${periodScore[1] === '5' ? '决胜期' : `第 ${periodScore[1]} 节`} · ${periodScore[2]} 队得分`;
  const finalScore = field.match(/^summary\.final\.(A|B)$/);
  if (finalScore) return `最后比分 · ${finalScore[1]} 队`;
  if (field === 'summary.winner') return '比赛结果 · 胜队';
  if (field === 'summary.ended_at') return '比赛结果 · 结束时间';
  if (field.startsWith('summary')) return '书面比分与比赛结果';
  const official = field.match(/^official\.([^.]+)\.name$/);
  if (official) return `工作人员 · ${officialLabels[official[1] as OfficialEntry['role']] ?? official[1]}`;
  if (field.startsWith('official')) return '工作人员';
  return '比赛基本信息';
}

const fieldLabels: Record<string, string> = {
  competition: '竞赛名称', game_number: '比赛序号', date: '日期', scheduled_time: '计划时间', venue: '地点',
  crew_chief: '主裁判员', umpire_1: '副裁判员 1', umpire_2: '副裁判员 2',
  name: '姓名', license_number: '证件号码', jersey_number: '球衣号码', captain: '队长', participation: '上场状态',
  code: '犯规类型', free_throws: '罚球数', cancelled: '抵消标记', period: '节次', minute: '比赛分钟', count: '数量',
  head_coach: '主教练员', assistant_coach: '助理教练员', team_a: 'A 队', team_b: 'B 队',
  points: '本次得分', cumulative_score: '累计分', scorer_jersey: '得分号码', boundary: '结束标记',
  winner_name: '胜队', ended_at: '结束时间', signature: '签名状态',
};

function formatChangePath(path: string) {
  const parts = path.split('/').slice(1).map((part) => part.replaceAll('~1', '/').replaceAll('~0', '~'));
  if (parts[0] === 'header') return `比赛信息 · ${fieldLabels[parts[1]] ?? parts[1]}`;
  if (parts[0] === 'teams') {
    const side = `${parts[1]} 队`;
    if (parts[2] === 'players') {
      const base = `${side} · 第 ${parts[3]} 行队员`;
      if (parts[4] === 'fouls') return `${base} · 第 ${parts[5]} 格犯规 · ${fieldLabels[parts[6]] ?? parts[6]}`;
      if (parts[4] === 'post_foul_markers') return `${base} · 附加犯规 · ${fieldLabels[parts[6]] ?? parts[6]}`;
      return `${base} · ${fieldLabels[parts[4]] ?? parts[4]}`;
    }
    if (parts[2] === 'timeouts') return `${side} · 暂停 ${parts[3]} · ${fieldLabels[parts[4]] ?? parts[4]}`;
    if (parts[2] === 'team_fouls') return `${side} · 第 ${parts[3]} 节全队犯规 · ${fieldLabels[parts[4]] ?? parts[4]}`;
    if (parts[2]?.includes('coach') && parts[2]?.includes('foul')) return `${side} · 教练犯规第 ${parts[3]} 格 · ${fieldLabels[parts[4]] ?? parts[4]}`;
    return `${side} · ${fieldLabels[parts[2]] ?? parts[2]}`;
  }
  if (parts[0] === 'score_events') return `第 ${parts[1]} 个得分事件 · ${fieldLabels[parts[2]] ?? parts[2]}`;
  if (parts[0] === 'stated_period_scores') return `第 ${parts[1]} 节书面比分 · ${fieldLabels[parts[2]] ?? parts[2]}`;
  if (parts[0] === 'final_score') return `比赛结果 · ${fieldLabels[parts[1]] ?? parts[1]}`;
  if (parts[0] === 'officials') return `裁判员 ${parts[1]} · ${fieldLabels[parts[2]] ?? parts[2]}`;
  if (parts[0] === 'table_personnel') return `记录台人员 · 第 ${Number(parts[1]) + 1} 项`;
  return parts.map((part) => fieldLabels[part] ?? part).join(' · ');
}

function formatChangeValue(value: unknown) {
  if (value === null || value === undefined || value === '') return '（空）';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export function Inspector({
  document,
  selectedField,
  validation,
  changes,
  recognitionRun = null,
  recognitionDiff = null,
  recognitionState = 'idle',
  onMutate,
  onSelect,
  onApplyRecognition = async () => {},
  onDismissRecognitionDiff = () => {},
  readOnly = false,
}: InspectorProps) {
  const inspectorRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const target = inspectorRef.current?.querySelector<HTMLElement>(
      '[data-precise-focus="true"]',
    );
    if (target && globalThis.document.activeElement !== target) target.focus();
  }, [selectedField]);

  const playerMatch = selectedField.match(/^team\.(A|B)\.player\.(\d{2})/);
  const teamMatch = selectedField.match(/^team\.(A|B)\.(?:meta|name|timeout|team_foul|coach|head_coach|assistant_coach)/);
  const scoreMatch = selectedField.match(/^score\.(A|B)\.(\d{3})/);

  let editor: React.ReactNode;
  if (playerMatch) {
    editor = <PlayerEditor document={document} side={playerMatch[1] as TeamSide} row={Number(playerMatch[2])} selectedField={selectedField} onMutate={onMutate} />;
  } else if (teamMatch) {
    editor = <TeamEditor document={document} side={teamMatch[1] as TeamSide} selectedField={selectedField} onMutate={onMutate} />;
  } else if (scoreMatch) {
    editor = <ScoreEditor document={document} side={scoreMatch[1] as TeamSide} cumulative={Number(scoreMatch[2])} selectedField={selectedField} onMutate={onMutate} onSelect={onSelect} />;
  } else if (selectedField === 'summary' || selectedField.startsWith('summary.')) {
    editor = <SummaryEditor document={document} selectedField={selectedField} onMutate={onMutate} />;
  } else if (selectedField === 'officials' || selectedField.startsWith('official.')) {
    editor = <OfficialsEditor document={document} selectedField={selectedField} onMutate={onMutate} />;
  } else {
    editor = <HeaderEditor document={document} selectedField={selectedField} onMutate={onMutate} />;
  }
  const preciseSelection = /^header\./.test(selectedField)
    || /^summary\./.test(selectedField)
    || /^official\./.test(selectedField)
    || /^team\.(A|B)\.(?:name|head_coach|assistant_coach|assistant_coach_foul|assistant_coach_post_foul|timeout|team_foul|coach_foul|coach_post_foul)/.test(selectedField)
    || /^team\.(A|B)\.player\.\d{2}\./.test(selectedField);
  const scorePreciseSelection = /^score\.(A|B)\.\d{3}\.edit$/.test(selectedField);

  return (
    <aside
      ref={inspectorRef}
      className={`inspector${readOnly ? ' is-read-only' : ''}`}
      aria-label="语义检查器"
      aria-readonly={readOnly}
    >
      <header className="inspector-context">
        <div>
          <span className="pane-kicker">当前选区</span>
          <strong>{selectionLabel(selectedField)}</strong>
        </div>
        <span className="selection-mode">{preciseSelection || scorePreciseSelection ? '精确格' : '区块'}</span>
      </header>
      <div className="inspector-scroll">
        <fieldset className="inspector-edit-fields" disabled={readOnly}>
          {editor}
          <RecognitionPanel
            run={recognitionRun}
            diff={recognitionDiff}
            state={recognitionState}
            document={document}
            problemPaths={document.recognition?.problem_paths ?? []}
            issues={document.recognition?.issues ?? []}
            tablePersonnel={document.recognition?.table_personnel ?? []}
            onApply={onApplyRecognition}
            onDismissDiff={onDismissRecognitionDiff}
            onLocateProblem={(path) => onSelect(pathToField(path, document, true))}
            onResolveProblem={(path, code) => onMutate((draft) => {
              if (!draft.recognition) return;
              if (code) {
                draft.recognition.issues = (draft.recognition.issues ?? []).filter(
                  (candidate) => candidate.path !== path || candidate.code !== code,
                );
              } else {
                draft.recognition.problem_paths = draft.recognition.problem_paths.filter(
                  (candidate) => candidate !== path,
                );
              }
            })}
          />
        </fieldset>
        <section className="validation-section">
          <div className="section-title-row">
            <div>
              <span className="pane-kicker">确定性检查</span>
              <h3>校验问题</h3>
            </div>
            {validation?.status === 'valid' ? <CheckCircle2 className="status-valid" size={20} /> : <AlertCircle className="status-muted" size={20} />}
          </div>
          {!validation ? (
            <p className="section-note">点击顶部“校验”后显示算术、名单和符号冲突。</p>
          ) : validation.issues.length === 0 ? (
            <div className="validation-empty"><CheckCircle2 size={16} /> 当前没有发现确定性冲突</div>
          ) : (
            <div className="issue-list">
              {validation.issues.map((issue, index) => (
                <button key={`${issue.code}-${index}`} className={`issue-row ${issue.severity}`} onClick={() => onSelect(pathToField(issue.paths[0] ?? '', document, true))}>
                  <span className="issue-code">{issue.code}</span>
                  <span>{issue.message}</span>
                </button>
              ))}
            </div>
          )}
        </section>
        <section className="change-log-section">
          <div className="section-title-row">
            <div>
              <span className="pane-kicker">只读审计</span>
              <h3>人工修改记录</h3>
            </div>
            <History size={18} />
          </div>
          {changes.length ? (
            <ol className="change-log-list">
              {changes.map((entry) => (
                <li key={entry.id}>
                  <details>
                    <summary>
                      <span>{entry.summary}</span>
                      <time><Clock3 size={12} />{new Date(entry.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}</time>
                    </summary>
                    {entry.changes.length ? (
                      <ul className="field-change-list">
                        {entry.changes.map((change) => (
                          <li key={change.path}>
                            <strong>{formatChangePath(change.path)}</strong>
                            <span><del>{formatChangeValue(change.before)}</del><i>→</i><ins>{formatChangeValue(change.after)}</ins></span>
                          </li>
                        ))}
                      </ul>
                    ) : <p>该操作只记录动作，不保存被替换的完整旧记录表。</p>}
                  </details>
                </li>
              ))}
            </ol>
          ) : (
            <p className="section-note">人工保存、撤销、重做、应用识别差异、重新上传和提交会记录在这里；视图操作与自动识别不计入。</p>
          )}
        </section>
      </div>
    </aside>
  );
}

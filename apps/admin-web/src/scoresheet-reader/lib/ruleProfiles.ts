import ruleProfilesJson from '../../../shared/rule_profiles.json';
import type { FoulCode, FoulMarkStyle, RuleProfileId } from '../types';

export type FoulEditorGroup = 'player' | 'coach' | 'post_foul';
export type FoulSuffix = '' | '1' | '2' | '3' | 'c';
export type FoulSubject = 'player' | 'head_coach' | 'assistant_coach' | 'post_foul';

interface FoulMarkingDefinition {
  id: string;
  code: FoulCode;
  style: FoulMarkStyle;
  subjects: string[];
  editor_groups: FoulEditorGroup[];
  allowed_suffixes: FoulSuffix[];
}

interface RuleProfileDefinition {
  label: string;
  effective_from: string;
  enabled_in_editor: boolean;
  foul_markings: FoulMarkingDefinition[];
}

export interface FoulEditorOption {
  code: FoulCode;
  catalogId: string;
  markStyle: FoulMarkStyle;
  allowedSuffixes: FoulSuffix[];
}

const ruleProfiles = ruleProfilesJson as Record<RuleProfileId, RuleProfileDefinition>;

export function ruleProfileLabel(profileId: RuleProfileId): string {
  return ruleProfiles[profileId].label;
}

export function foulEditorOptions(
  profileId: RuleProfileId,
  group: FoulEditorGroup,
): FoulEditorOption[] {
  const options: FoulEditorOption[] = [];
  for (const marking of ruleProfiles[profileId].foul_markings) {
    if (!marking.editor_groups.includes(group)) continue;
    const existing = options.find(
      (option) => option.code === marking.code && option.markStyle === marking.style,
    );
    if (existing) {
      for (const suffix of marking.allowed_suffixes) {
        if (!existing.allowedSuffixes.includes(suffix)) existing.allowedSuffixes.push(suffix);
      }
      continue;
    }
    options.push({
      code: marking.code,
      catalogId: marking.id,
      markStyle: marking.style,
      allowedSuffixes: [...marking.allowed_suffixes],
    });
  }
  return options;
}

export function foulOptionLabel(options: FoulEditorOption[]): string {
  return options.map((option) => option.code).join(' / ');
}

export function ruleProfileAllowsFoulMarking(
  profileId: RuleProfileId,
  code: FoulCode,
  markStyle: FoulMarkStyle = 'plain',
  subject?: FoulSubject,
  suffix?: FoulSuffix,
): boolean {
  return ruleProfiles[profileId].foul_markings.some(
    (marking) => marking.code === code
      && marking.style === markStyle
      && (subject === undefined || marking.subjects.includes(subject))
      && (suffix === undefined || marking.allowed_suffixes.includes(suffix)),
  );
}

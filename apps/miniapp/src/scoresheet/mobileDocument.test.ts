import { describe, expect, it } from "vitest";

import {
  type CanonicalScoresheetDocument,
  mergeMobileDocument,
  projectScoresheetDetail,
} from "./mobileDocument";

function canonicalDraft(): CanonicalScoresheetDocument {
  return {
    schema_version: "1.4.0",
    rules_profile: "fiba_2024",
    id: "sheet-1",
    revision: 7,
    template_id: "pku-basketball-2019-v1",
    status: "needs_review",
    created_at: "2026-08-21T00:00:00Z",
    updated_at: "2026-08-21T00:00:00Z",
    source: {
      original_filename: "sheet.jpg",
      original_url: "",
      aligned_url: "",
      version: 1,
      content_sha256: "abc",
      width: 1200,
      height: 1800,
      rotation: 0,
      corners: null,
    },
    game_prior: {
      game_id: "game-1",
      competition: "北大杯",
      division: "男甲",
      date: "2026-08-21",
      scheduled_time: "12:50",
      venue: "第一体育馆",
      team_a: { team_id: "team-a", name: "甲队", player_names: ["甲一"] },
      team_b: { team_id: "team-b", name: "乙队", player_names: ["乙一"] },
      source_hash: "prior",
      locked_paths: [],
    },
    recognition: {
      run_id: "run-1",
      notes: "keep me",
      table_personnel: ["记录员甲"],
      problem_paths: ["/teams/0/players/0/license_number"],
      issues: [],
      applied_at: "2026-08-21T00:00:00Z",
    },
    header: {
      competition: "北大杯",
      game_number: "M-01",
      date: "2026-08-21",
      scheduled_time: "12:50",
      venue: "第一体育馆",
      crew_chief: "裁判甲",
      umpire_1: "裁判乙",
      umpire_2: "裁判丙",
    },
    teams: [
      {
        side: "A",
        name: "甲队",
        players: [{
          row: 1,
          license_number: "LIC-A-1",
          name: "甲一",
          jersey_number: "7",
          captain: true,
          participation: "starter",
          fouls: [{ slot: 1, code: "P", catalog_id: "P1", mark_style: "circled", free_throws: 2, cancelled: false, period: 5 }],
          post_foul_markers: [{ slot: 1, code: "GD", catalog_id: null, mark_style: "plain", free_throws: null, cancelled: false, period: 4 }],
        }],
        timeouts: [{ scope: "H1", slot: 1, minute: 4 }],
        team_fouls: [{ period: 1, count: 2 }],
        coach_fouls: [],
        coach_post_foul_markers: [{ slot: 1, code: "GD", catalog_id: null, mark_style: "plain", free_throws: null, cancelled: false, period: 4 }],
        assistant_coach_fouls: [],
        assistant_coach_post_foul_markers: [],
        head_coach: "教练甲",
        assistant_coach: "助教甲",
      },
      {
        side: "B",
        name: "乙队",
        players: [{
          row: 1,
          license_number: "LIC-B-1",
          name: "乙一",
          jersey_number: "9",
          captain: false,
          participation: "substitute",
          fouls: [],
          post_foul_markers: [],
        }],
        timeouts: [],
        team_fouls: [],
        coach_fouls: [],
        coach_post_foul_markers: [],
        assistant_coach_fouls: [],
        assistant_coach_post_foul_markers: [],
        head_coach: "教练乙",
        assistant_coach: "",
      },
    ],
    score_events: [{
      sequence: 1,
      team: "A",
      period: 5,
      points: null,
      cumulative_score: 1,
      scorer_jersey: "7",
      mark: null,
      scorer_circled: false,
      boundary: "none",
      ink_role: "neutral",
    }],
    stated_period_scores: [
      { period: 1, team_a: 10, team_b: 8 },
      { period: 5, team_a: 5, team_b: 3 },
    ],
    final_score: { team_a: 15, team_b: 11, winner_name: "甲队", ended_at: "14:10" },
    officials: [
      { role: "scorer", name: "记录员甲", signature: "absent" },
      { role: "crew_chief", name: "裁判甲", signature: "unclear" },
      { role: "protest_captain", name: "甲一", signature: "absent" },
    ],
    acknowledged_warnings: [],
  } as CanonicalScoresheetDocument;
}

function detail(draft: CanonicalScoresheetDocument) {
  return {
    id: "sheet-1",
    game: { label: "男甲 · 甲队 vs 乙队" },
    source: null,
    source_version: 1,
    status: "DRAFT",
    draft,
    draft_version: 7,
    event_sequence: 4,
    reviewed_regions: {},
    validation_report: {
      errors: [{ id: "issue", severity: "ERROR", code: "X", region: "RUNNING_SCORE", path: "/score_events/0/points", message: "待补分值", context: {} }],
      warnings: [],
      computed: {},
    },
    validation_draft_version: null,
    acknowledged_warnings: [],
    recognition: null,
    lease: null,
    publication: null,
  } as never;
}

describe("mobile scoresheet projection", () => {
  it("projects canonical paths and round-trips fields absent from the mobile editor", () => {
    const original = canonicalDraft();
    const projection = projectScoresheetDetail(detail(original));

    expect(projection.detail.draft.game.scheduled_time).toBe("12:50");
    expect(projection.detail.validation_report.errors?.[0].path).toBe("/running_score/0/points");

    projection.detail.draft.game.game_number = "M-02";
    const merged = mergeMobileDocument(projection.detail.draft, projection.canonical) as CanonicalScoresheetDocument;

    expect(merged.header.game_number).toBe("M-02");
    expect(merged.teams[0].players[0].license_number).toBe("LIC-A-1");
    expect(merged.teams[0].players[0].post_foul_markers[0].code).toBe("GD");
    expect(merged.teams[0].players[0].fouls[0].period).toBe(5);
    expect(merged.teams[0].coach_post_foul_markers[0].code).toBe("GD");
    expect(merged.score_events[0].period).toBe(5);
    expect(merged.score_events[0].points).toBeNull();
    expect(merged.stated_period_scores.find((row) => row.period === 5)).toMatchObject({ team_a: 5, team_b: 3 });
    expect(merged.officials.find((row) => row.role === "crew_chief")?.signature).toBe("unclear");
    expect(merged.recognition?.notes).toBe("keep me");
    expect(merged.table_personnel).toBeUndefined();
    expect(merged.recognition?.table_personnel).toEqual(["记录员甲"]);
  });

  it("rejects legacy periods above the combined overtime slot instead of folding them", () => {
    const original = canonicalDraft();
    original.score_events[0].period = 6 as never;

    expect(() => projectScoresheetDetail(detail(original))).toThrow(
      "记录表包含不支持的节次：6",
    );
  });
});

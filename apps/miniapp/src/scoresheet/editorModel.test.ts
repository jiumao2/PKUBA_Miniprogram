import { describe, expect, it } from "vitest";

import type { ScoresheetDocument, TeamEntry } from "@pkuba/scoresheet-domain";
import {
  addRecognitionPersonnel,
  compactRoster,
  removeRecognitionPersonnel,
  setOfficialName,
  setPlayerRow,
  setRecognitionPersonnel,
  setTeamFoulCount,
  setTeamTimeoutMinute,
  updateRecognitionPersonnel,
} from "./editorModel";

function team(side: "A" | "B"): TeamEntry {
  return {
    side,
    name: `${side}队`,
    players: [],
    timeouts: [],
    team_fouls: [],
    coach_fouls: [],
    coach_post_foul_markers: [],
    assistant_coach_fouls: [],
    assistant_coach_post_foul_markers: [],
    head_coach: "",
    assistant_coach: "",
  };
}

function draft(): ScoresheetDocument {
  return {
    schema_version: "1.4.0", id: "s", revision: 0, template_id: "t", status: "draft",
    created_at: "", updated_at: "",
    source: { original_filename: "", original_url: "", aligned_url: "", width: 1, height: 1, rotation: 0, corners: [[0, 0], [1, 0], [1, 1], [0, 1]] },
    game_prior: null,
    recognition: { run_id: "r", notes: "", table_personnel: ["原人员"], problem_paths: [], applied_at: "" },
    header: { competition: "", game_number: "", date: "", scheduled_time: "", venue: "", crew_chief: "", umpire_1: "", umpire_2: "" },
    teams: [team("A"), team("B")], score_events: [], stated_period_scores: [],
    final_score: { team_a: 0, team_b: 0, winner_name: "", ended_at: "" }, officials: [], acknowledged_warnings: [],
  };
}

describe("miniapp canonical editor model", () => {
  it("round-trips all player paper fields while persisting only material rows", () => {
    const source = draft();
    const player = {
      ...compactRoster(source, "A")[4],
      license_number: "2026005",
      name: "王五",
      jersey_number: "00",
      participation: "starter" as const,
      captain: true,
      fouls: [{ slot: 1, code: "P" as const, free_throws: 2, cancelled: false, period: 3 as const }],
      post_foul_markers: [{ slot: 1, code: "GD" as const, free_throws: null, cancelled: false, period: 5 as const }],
    };
    const result = setPlayerRow(source, "A", 5, player);
    expect(result.teams[0].players).toHaveLength(1);
    expect(result.teams[0].players[0]).toEqual(player);
    expect(compactRoster(result, "A")[4]).toEqual(player);
    expect(result.source.corners).toEqual(source.source.corners);
  });

  it("removes a cleared paper row instead of publishing a blank player", () => {
    const source = draft();
    const filled = { ...compactRoster(source, "A")[2], name: "张三", jersey_number: "7" };
    const withPlayer = setPlayerRow(source, "A", 3, filled);
    const cleared = setPlayerRow(withPlayer, "A", 3, compactRoster(source, "A")[2]);
    expect(cleared.teams[0].players).toEqual([]);
    expect(compactRoster(cleared, "A")).toHaveLength(12);
  });

  it("persists team fouls, all official roles, and recognition personnel without projections", () => {
    let result = setTeamFoulCount(draft(), "B", 4, 3);
    result = setOfficialName(result, "umpire_2", "李裁判");
    result = setRecognitionPersonnel(result, ["张三", "李四"]);
    expect(result.teams[1].team_fouls).toEqual([{ period: 4, count: 3 }]);
    expect(result.officials).toContainEqual({ role: "umpire_2", name: "李裁判", signature: "absent" });
    expect(result.recognition?.table_personnel).toEqual(["张三", "李四"]);
  });

  it("creates an editable unassigned personnel list without a recognition result", () => {
    const source = draft();
    source.recognition = null;

    const result = setRecognitionPersonnel(source, ["无法归类人员"]);

    expect(result.recognition?.table_personnel).toEqual(["无法归类人员"]);
    expect(result.recognition?.run_id).toBe("manual-table-personnel");
  });

  it("does not persist a blank personnel row before the user enters a name", () => {
    const source = draft();
    source.recognition = null;

    const blank = addRecognitionPersonnel(source, "   ");
    const named = addRecognitionPersonnel(blank, "  待确认姓名  ");

    expect(blank.recognition).toBeNull();
    expect(named.recognition?.table_personnel).toEqual(["待确认姓名"]);
  });

  it("updates and removes a persisted unassigned personnel row by index", () => {
    const source = draft();
    const updated = updateRecognitionPersonnel(source, 0, "修改后人员");
    const removed = removeRecognitionPersonnel(updated, 0);

    expect(updated.recognition?.table_personnel).toEqual(["修改后人员"]);
    expect(removed.recognition?.table_personnel).toEqual([]);
  });

  it("updates, increments, and clears a timeout minute in the canonical draft", () => {
    let result = setTeamTimeoutMinute(draft(), "A", "H2", 2, 4);
    expect(result.teams[0].timeouts).toEqual([{ scope: "H2", slot: 2, minute: 4 }]);
    result = setTeamTimeoutMinute(result, "A", "H2", 2, 5);
    expect(result.teams[0].timeouts).toEqual([{ scope: "H2", slot: 2, minute: 5 }]);
    result = setTeamTimeoutMinute(result, "A", "H2", 2, null);
    expect(result.teams[0].timeouts).toEqual([]);
  });
});

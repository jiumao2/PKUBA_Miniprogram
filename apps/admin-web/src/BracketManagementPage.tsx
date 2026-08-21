import { useEffect, useMemo, useRef, useState } from "react";
import type {
  AdminSeason,
  BracketManagement,
  WinnerFeedRelation,
} from "@pkuba/api-client";

import "./bracket-management.css";

type AdminClient = ReturnType<typeof import("@pkuba/api-client").createAdminClient>;

const stageLabels: Record<string, string> = {
  KNOCKOUT: "淘汰赛",
  SEMIFINAL: "半决赛",
  FINAL: "决赛",
};

function relationKey(targetGameId: string, side: string) {
  return `${targetGameId}:${side}`;
}

function initialRelations(data: BracketManagement): WinnerFeedRelation[] {
  if (data.feeds.length) {
    return data.feeds.map((item) => ({
      source_game_id: item.source_game_id,
      target_game_id: item.target_game_id,
      target_side: item.target_side,
    }));
  }
  return data.legacy_suggestions.map((item) => ({ ...item }));
}

export function BracketManagementPage({
  client,
  seasons,
  seasonId,
  onSeasonChange,
}: {
  client: AdminClient;
  seasons: AdminSeason[];
  seasonId: string;
  onSeasonChange: (seasonId: string) => void;
}) {
  const season = seasons.find((item) => item.id === seasonId) ?? seasons[0];
  const [divisionId, setDivisionId] = useState("");
  const [data, setData] = useState<BracketManagement | null>(null);
  const [relations, setRelations] = useState<WinnerFeedRelation[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadRequestId = useRef(0);

  const divisions = season?.divisions ?? [];

  useEffect(() => {
    if (!divisions.some((item) => item.id === divisionId)) {
      setDivisionId(divisions[0]?.id ?? "");
    }
  }, [seasonId, divisions, divisionId]);

  const load = async () => {
    const requestId = ++loadRequestId.current;
    if (!season || !divisionId || !divisions.some((item) => item.id === divisionId)) {
      setData(null);
      setRelations([]);
      setError(null);
      setMessage(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    setMessage(null);
    try {
      const next = await client.getBracketManagement(season.id, divisionId);
      if (requestId !== loadRequestId.current) return;
      setData(next);
      setRelations(initialRelations(next));
    } catch (reason: unknown) {
      if (requestId !== loadRequestId.current) return;
      setError(reason instanceof Error ? reason.message : "无法读取淘汰赛关系");
    } finally {
      if (requestId === loadRequestId.current) setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [seasonId, divisionId]);

  const relationMap = useMemo(
    () => new Map(relations.map((item) => [relationKey(item.target_game_id, item.target_side), item])),
    [relations],
  );
  const outgoingSourceIds = useMemo(
    () => new Set(relations.map((item) => item.source_game_id)),
    [relations],
  );
  const rounds = useMemo(() => {
    if (!data) return [];
    const grouped = new Map<string, BracketManagement["games"]>();
    data.games.forEach((game) => {
      const key = `${game.round_number}:${game.stage}`;
      grouped.set(key, [...(grouped.get(key) ?? []), game]);
    });
    return [...grouped.entries()]
      .sort(([left], [right]) => Number(left.split(":")[0]) - Number(right.split(":")[0]))
      .map(([key, games]) => ({ key, games, stage: games[0]?.stage ?? "" }));
  }, [data]);

  const updateRelation = (targetGameId: string, targetSide: string, sourceGameId: string) => {
    const key = relationKey(targetGameId, targetSide);
    const next = relations.filter(
      (item) => relationKey(item.target_game_id, item.target_side) !== key,
    );
    if (sourceGameId) {
      next.push({
        source_game_id: sourceGameId,
        target_game_id: targetGameId,
        target_side: targetSide,
      });
    }
    setRelations(next);
    setMessage(null);
  };

  const saveRelations = async () => {
    if (!data) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const payload = {
        expected_season_version: data.season_version,
        expected_division_version: data.division_version,
        relations,
      };
      const preview = await client.previewBracketRelations(
        data.season_id,
        data.division_id,
        payload,
      );
      if (!preview.can_apply) {
        setError(preview.blockers.map((item) => item.message).join("\n"));
        return;
      }
      const summary = [
        `新增 ${preview.added_count} 条，移除 ${preview.removed_count} 条，保留 ${preview.unchanged_count} 条。`,
        "确认后这些关系将成为胜者自动晋级的唯一权威来源。",
      ].join("\n");
      if (!window.confirm(`${summary}\n\n确认保存淘汰赛关系？`)) return;
      const updated = await client.applyBracketRelations(
        data.season_id,
        data.division_id,
        { ...payload, impact_hash: preview.impact_hash },
      );
      setData(updated);
      setRelations(initialRelations(updated));
      setMessage("淘汰赛关系已确认，后续赛果会按该链路自动传递胜者。");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "淘汰赛关系保存失败");
    } finally {
      setBusy(false);
    }
  };

  const correct = async (gameId: string, version: number, code: string) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const preview = await client.previewBracketCorrection(gameId, version);
      if (!preview.can_apply) {
        setError(preview.blockers.map((item) => item.message).join("\n"));
        return;
      }
      if (preview.affected_game_count === 0) {
        setMessage(`${code} 没有需要重置的下游比赛，可直接到赛程编辑器纠正。`);
        return;
      }
      const summary = [
        `${code} 将重置 ${preview.affected_game_count} 场下游比赛。`,
        `取消 ${preview.active_request_count} 个活动调赛，释放 ${preview.active_reservation_count} 个预留。`,
        `撤回 ${preview.publication_count} 份当前发布记录，但保留历史修订、照片和审计。`,
      ].join("\n");
      if (!window.confirm(`${summary}\n\n确认执行级联重置？`)) return;
      await client.applyBracketCorrection(gameId, version, preview.impact_hash);
      setMessage(`${code} 的下游数据已重置，现在可以纠正来源比赛赛果。`);
      await load();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "下游纠错失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bracket-management">
      <div className="bracket-toolbar">
        <label>
          赛季
          <select value={season?.id ?? ""} onChange={(event) => onSeasonChange(event.target.value)}>
            {seasons.map((item) => (
              <option key={item.id} value={item.id}>{item.year} · {item.name}</option>
            ))}
          </select>
        </label>
        <label>
          组别
          <select value={divisionId} onChange={(event) => setDivisionId(event.target.value)}>
            {divisions.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
        <button className="secondary-action" type="button" onClick={() => void load()}>
          重新读取
        </button>
      </div>

      {loading && <div className="bracket-state">正在读取淘汰赛关系…</div>}
      {!loading && !data && <div className="bracket-state">当前组别尚无淘汰赛。</div>}
      {data && (
        <>
          <section className={`bracket-authority ${data.relation_mode.toLowerCase()}`}>
            <div>
              <span>关系来源</span>
              <strong>{data.relation_mode === "AUTHORITATIVE" ? "权威链路" : "历史推导"}</strong>
              <p>
                {data.relation_mode === "AUTHORITATIVE"
                  ? "服务端按已确认关系自动传递胜者。"
                  : "当前仅按旧赛程推导展示；保存前不会改写任何比赛。"}
              </p>
            </div>
            <div>
              <span>组别状态</span>
              <strong>{data.division_name}</strong>
              <p>{data.locked_reason || "可编辑胜者来源，并在保存前预览全部变化。"}</p>
            </div>
            <button
              className="primary-action"
              disabled={busy || data.read_only}
              type="button"
              onClick={() => void saveRelations()}
            >
              {busy ? "正在处理…" : data.relation_mode === "AUTHORITATIVE" ? "预览并保存" : "确认历史关系"}
            </button>
          </section>

          <section className="bracket-board" aria-label={`${data.division_name}淘汰赛关系`}>
            {rounds.map((round, roundIndex) => (
              <div className="bracket-round" key={round.key}>
                <header>
                  <span>ROUND {roundIndex + 1}</span>
                  <h2>{stageLabels[round.stage] ?? round.stage}</h2>
                </header>
                {round.games.map((game) => {
                  const previousGames = data.games.filter(
                    (candidate) =>
                      candidate.id !== game.id &&
                      `${candidate.date}T${candidate.start_time}` < `${game.date}T${game.start_time}`,
                  );
                  const score = game.home_score === null || game.away_score === null
                    ? "—"
                    : `${game.home_score} : ${game.away_score}`;
                  return (
                    <article className="bracket-game-card" key={game.id}>
                      <div className="bracket-game-meta">
                        <strong>{game.code}</strong>
                        <span>{game.date} · {game.start_time.slice(0, 5)}</span>
                      </div>
                      {(["HOME", "AWAY"] as const).map((side) => {
                        const relation = relationMap.get(relationKey(game.id, side));
                        const teamName = side === "HOME" ? game.home_name : game.away_name;
                        return (
                          <div className="bracket-team-line" key={side}>
                            <span>{side === "HOME" ? "主" : "客"}</span>
                            <strong>{teamName}</strong>
                            {previousGames.length > 0 && (
                              <label>
                                胜者来源
                                <select
                                  aria-label={`${game.code}${side === "HOME" ? "主队" : "客队"}胜者来源`}
                                  disabled={busy || data.read_only}
                                  value={relation?.source_game_id ?? ""}
                                  onChange={(event) => updateRelation(game.id, side, event.target.value)}
                                >
                                  <option value="">固定球队 / 独立录入</option>
                                  {previousGames.map((source) => (
                                    <option key={source.id} value={source.id}>
                                      {source.code} 胜者 · {source.home_name} / {source.away_name}
                                    </option>
                                  ))}
                                </select>
                              </label>
                            )}
                          </div>
                        );
                      })}
                      <div className="bracket-game-result">
                        <span>{game.status === "COMPLETED" ? "已结束" : game.status === "FORFEIT" ? "弃权" : "待赛"}</span>
                        <strong>{score}</strong>
                        {outgoingSourceIds.has(game.id) && game.status !== "SCHEDULED" && (
                          <button
                            className="text-action"
                            disabled={busy || data.read_only}
                            type="button"
                            onClick={() => void correct(game.id, game.version, game.code)}
                          >
                            级联纠错
                          </button>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            ))}
          </section>
        </>
      )}
      {message && <p className="bracket-message success" role="status">{message}</p>}
      {error && <pre className="bracket-message error" role="alert">{error}</pre>}
    </div>
  );
}

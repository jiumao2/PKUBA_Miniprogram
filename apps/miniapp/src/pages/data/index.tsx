import { Button, ScrollView, Text, View } from "@tarojs/components";
import { useDidShow } from "@tarojs/taro";
import { useEffect, useRef, useState } from "react";
import type {
  Division,
  PlayerLeaderboardItem,
  PublicScoresheetStat,
  PublishedGameSummary,
  TeamLeaderboardItem,
} from "@pkuba/api-client";

import { api } from "../../api";
import { syncTabBar } from "../../tabbar";
import {
  loadCompleteList,
  PLAYER_PAGE_SIZE,
  playerPageCount,
  playerVisibleTotal,
} from "./pagination";
import "./index.css";

type Tab = "teams" | "players" | "games";
type Order = "asc" | "desc";

const TEAM_SORTS = [
  ["points_per_game", "场均得分"], ["total_points", "总得分"],
  ["points_against_per_game", "场均失分"], ["point_difference_per_game", "场均净胜"],
  ["win_percentage", "胜率"], ["wins", "胜场"], ["games_played", "场次"],
] as const;
const PLAYER_SORTS = [
  ["points_per_game", "场均得分"], ["total_points", "总得分"],
  ["games_played", "出场"], ["starts", "首发"], ["one_point_events", "罚球"],
  ["two_point_events", "两分球"], ["three_point_events", "三分球"],
  ["fouls_per_game", "场均犯规"],
] as const;

export default function DataPage() {
  const [tab, setTab] = useState<Tab>("teams");
  const [divisions, setDivisions] = useState<Division[]>([]);
  const [divisionId, setDivisionId] = useState("");
  const [teamSort, setTeamSort] = useState("points_per_game");
  const [playerSort, setPlayerSort] = useState("points_per_game");
  const [order, setOrder] = useState<Order>("desc");
  const [teams, setTeams] = useState<TeamLeaderboardItem[]>([]);
  const [players, setPlayers] = useState<PlayerLeaderboardItem[]>([]);
  const [playerPage, setPlayerPage] = useState(1);
  const [playerTotal, setPlayerTotal] = useState(0);
  const [games, setGames] = useState<PublishedGameSummary[]>([]);
  const [selected, setSelected] = useState<PublicScoresheetStat | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requestVersion = useRef(0);

  useDidShow(() => syncTabBar(3));
  useEffect(() => {
    void api.getCurrentSeason().then((season) => {
      setDivisions(season.divisions);
      const initial = season.divisions.find((division) => division.code === "men-a") ?? season.divisions[0];
      setDivisionId((current) => current || initial?.id || "");
    }).catch((reason) => {
      setError(messageOf(reason));
      setLoading(false);
    });
  }, []);
  useEffect(() => {
    if (!divisionId) return;
    const version = requestVersion.current + 1;
    requestVersion.current = version;
    setLoading(true);
    setError("");
    const division = `division_id=${encodeURIComponent(divisionId)}`;
    const task = tab === "teams"
      ? loadCompleteList(
          (page, pageSize) => api.getTeamLeaderboard(
            `?${division}&sort=${teamSort}&order=${order}&page=${page}&page_size=${pageSize}`,
          ),
          (row) => row.team_id,
        ).then((rows) => {
          if (requestVersion.current === version) setTeams(rows);
        })
      : tab === "players"
        ? api.getPlayerLeaderboard(
            `?${division}&sort=${playerSort}&order=${order}&page=${playerPage}&page_size=${PLAYER_PAGE_SIZE}`,
          ).then((page) => {
            if (requestVersion.current !== version) return;
            setPlayers(page.items);
            setPlayerTotal(page.total);
          })
        : loadCompleteList(
            (page, pageSize) => api.getPublishedGameSummaries(
              `?${division}&page=${page}&page_size=${pageSize}`,
            ),
            (row) => row.publication_id,
          ).then((rows) => {
            if (requestVersion.current === version) setGames(rows);
          });
    void task.catch((reason) => {
      if (requestVersion.current === version) setError(messageOf(reason));
    }).finally(() => {
      if (requestVersion.current === version) setLoading(false);
    });
  }, [divisionId, order, playerPage, playerSort, tab, teamSort]);

  const sorts = tab === "teams" ? TEAM_SORTS : PLAYER_SORTS;
  function selectSort(nextSort: string) {
    const current = tab === "teams" ? teamSort : playerSort;
    setPlayerPage(1);
    if (nextSort === current) setOrder((value) => value === "desc" ? "asc" : "desc");
    else {
      if (tab === "teams") setTeamSort(nextSort); else setPlayerSort(nextSort);
      setOrder("desc");
    }
  }
  async function openGame(gameId: string) {
    setLoading(true);
    try {
      const rows = await api.getPublicScoresheetStats(gameId);
      setSelected(rows[0] ?? null);
    } catch (reason) { setError(messageOf(reason)); }
    finally { setLoading(false); }
  }

  return <View className="page data-page">
    <View className="data-sticky">
      <View className="data-tabs">
        {(["teams", "players", "games"] as Tab[]).map((value) => <Button
          className={tab === value ? "data-tab active" : "data-tab"} key={value}
          onClick={() => { setTab(value); setSelected(null); setOrder("desc"); setPlayerPage(1); }}
        >{value === "teams" ? "球队" : value === "players" ? "球员" : "单场"}</Button>)}
      </View>
      <ScrollView className="division-strip" scrollX showScrollbar={false}>
        <View className="filter-row">{divisions.map((division) => <Button
          className={divisionId === division.id ? "filter-chip active" : "filter-chip"}
          key={division.id} onClick={() => { setDivisionId(division.id); setPlayerPage(1); }}
        >{division.name}</Button>)}</View>
      </ScrollView>
    </View>
    <View className="data-heading">
      <Text className="page-title">{tab === "teams" ? "球队榜" : tab === "players" ? "球员榜" : "单场数据"}</Text>
      {!loading && !error && tab === "teams" && <Text className="data-count">共 {teams.length} 队</Text>}
      {!loading && !error && tab === "players" && <Text className="data-count">
        {playerTotal > 100 ? "前 100 名" : `共 ${playerTotal} 名`}
      </Text>}
      {!loading && !error && tab === "games" && <Text className="data-count">共 {games.length} 场</Text>}
    </View>
    {tab !== "games" && <ScrollView className="sort-strip" scrollX showScrollbar={false}>
      <View className="filter-row">{sorts.map(([value, label]) => {
        const active = value === (tab === "teams" ? teamSort : playerSort);
        return <Button className={active ? "sort-chip active" : "sort-chip"} key={value} onClick={() => selectSort(value)}>
          {label}{active ? (order === "desc" ? " ↓" : " ↑") : ""}
        </Button>;
      })}</View>
    </ScrollView>}
    {loading && <State title="正在读取数据…" />}
    {error && <State title={error} tone="error" />}
    {!loading && !error && tab === "teams" && <TeamTable rows={teams} sort={teamSort} />}
    {!loading && !error && tab === "players" && <View>
      <PlayerTable rows={players} sort={playerSort} />
      <PlayerPager
        page={playerPage}
        total={playerTotal}
        onPageChange={setPlayerPage}
      />
    </View>}
    {!loading && !error && tab === "games" && (selected
      ? <GameDetail game={selected} onBack={() => setSelected(null)} />
      : <GameList games={games} onOpen={(id) => void openGame(id)} />)}
  </View>;
}

function PlayerPager({ page, total, onPageChange }: {
  page: number;
  total: number;
  onPageChange: (page: number) => void;
}) {
  const pages = playerPageCount(total);
  if (pages <= 1) return null;
  return <View className="player-pagination">
    <Text className="pagination-summary">
      第 {page}/{pages} 页 · {playerVisibleTotal(total)} 名
    </Text>
    <View className="pagination-controls">
      <Button
        className="page-step"
        disabled={page === 1}
        onClick={() => onPageChange(page - 1)}
      >上一页</Button>
      {Array.from({ length: pages }, (_, index) => index + 1).map((value) => <Button
        className={page === value ? "page-number active" : "page-number"}
        key={value}
        onClick={() => onPageChange(value)}
      >{value}</Button>)}
      <Button
        className="page-step"
        disabled={page === pages}
        onClick={() => onPageChange(page + 1)}
      >下一页</Button>
    </View>
  </View>;
}

function TeamTable({ rows, sort }: { rows: TeamLeaderboardItem[]; sort: string }) {
  if (!rows.length) return <State title="暂无球队数据" />;
  return <View className="leaderboard">
    <View className="leader-row table-head"><Text>排名</Text><Text>球队</Text><Text>主要数据</Text><Text>战绩</Text></View>
    {rows.map((row) => <View className={`leader-row ${row.division_gender === "WOMEN" ? "women" : ""}`} key={row.team_id}>
      <Text className="rank-number">{row.rank}</Text>
      <View className="identity-cell"><Text>{row.team_name}</Text><Text>{row.division_name}</Text></View>
      <View className="metric-cell"><Text>{teamMetric(row, sort)}</Text><Text>{teamMetricLabel(sort)}</Text></View>
      <View className="record-cell"><Text>{row.wins}-{row.losses}</Text><Text>{row.points_for}:{row.points_against}</Text></View>
    </View>)}
  </View>;
}

function PlayerTable({ rows, sort }: { rows: PlayerLeaderboardItem[]; sort: string }) {
  if (!rows.length) return <State title="暂无球员数据" />;
  return <View className="leaderboard">
    <View className="leader-row table-head"><Text>排名</Text><Text>球员</Text><Text>主要数据</Text><Text>出场</Text></View>
    {rows.map((row) => <View className={`leader-row ${row.division_gender === "WOMEN" ? "women" : ""}`} key={row.player_id}>
      <Text className="rank-number">{row.rank}</Text>
      <View className="identity-cell"><Text>#{row.jersey_number || "–"} {row.player_name}</Text><Text>{row.team_name} · {row.division_name}</Text></View>
      <View className="metric-cell"><Text>{playerMetric(row, sort)}</Text><Text>{playerMetricLabel(sort)}</Text></View>
      <View className="record-cell"><Text>{row.games_played}</Text><Text>首发 {row.starts}</Text></View>
    </View>)}
  </View>;
}

function GameList({ games, onOpen }: { games: PublishedGameSummary[]; onOpen: (id: string) => void }) {
  if (!games.length) return <State title="暂无单场数据" />;
  return <View className="game-list">{games.map((game) => <Button
    className={`game-row ${game.division_gender === "WOMEN" ? "women" : ""}`}
    key={game.game_id} onClick={() => onOpen(game.game_id)}
  ><View><Text>{game.date.slice(5)} · {game.start_time}</Text><Text>{game.division_name}</Text></View>
    <View className="game-matchup"><Text>{game.home_name}</Text><Text className="game-score">{game.home_score}:{game.away_score}</Text><Text>{game.away_name}</Text></View>
    <Text className="chevron">›</Text></Button>)}</View>;
}

function GameDetail({ game, onBack }: { game: PublicScoresheetStat; onBack: () => void }) {
  const players = game.player_stats.filter((row) => row.appeared || row.points || row.personal_fouls);
  return <View className="game-detail">
    <Button className="back-button" onClick={onBack}>‹ 返回场次</Button>
    <View className="detail-score"><Text>{game.home_name}</Text><Text>{game.home_score}:{game.away_score}</Text><Text>{game.away_name}</Text></View>
    <Text className="detail-meta">{game.date} · {game.start_time} · {game.division_name}</Text>
    <View className="player-detail-head"><Text>球员</Text><Text>得分</Text><Text>1/2/3分</Text><Text>犯规</Text></View>
    {players.map((player) => <View className="player-detail-row" key={`${player.team_id}-${player.player_id ?? player.player_name}`}>
      <View><Text>#{player.jersey_number || "–"} {player.player_name}</Text><Text>{player.team_name}{player.starter ? " · 首发" : ""}</Text></View>
      <Text>{player.points}</Text><Text>{player.one_point_events}/{player.two_point_events}/{player.three_point_events}</Text><Text>{player.personal_fouls}</Text>
    </View>)}
  </View>;
}

function State({ title, tone = "" }: { title: string; tone?: string }) {
  return <View className={`state data-state ${tone}`}><Text className="state-title">{title}</Text></View>;
}
function teamMetric(row: TeamLeaderboardItem, sort: string) {
  const values: Record<string, string | number> = {
    points_per_game: row.points_per_game.toFixed(1), total_points: row.points_for,
    points_against_per_game: row.points_against_per_game.toFixed(1),
    point_difference_per_game: signed(row.point_difference_per_game),
    win_percentage: `${row.win_percentage.toFixed(1)}%`, wins: row.wins, games_played: row.games_played,
  };
  return values[sort];
}
function teamMetricLabel(sort: string) { return TEAM_SORTS.find(([value]) => value === sort)?.[1] ?? ""; }
function playerMetric(row: PlayerLeaderboardItem, sort: string) {
  const values: Record<string, string | number> = {
    points_per_game: row.points_per_game.toFixed(1), total_points: row.total_points,
    games_played: row.games_played, starts: row.starts, one_point_events: row.one_point_events,
    two_point_events: row.two_point_events, three_point_events: row.three_point_events,
    fouls_per_game: row.fouls_per_game.toFixed(1),
  };
  return values[sort];
}
function playerMetricLabel(sort: string) { return PLAYER_SORTS.find(([value]) => value === sort)?.[1] ?? ""; }
function signed(value: number) { return `${value > 0 ? "+" : ""}${value.toFixed(1)}`; }
function messageOf(reason: unknown) { return reason instanceof Error ? reason.message : "数据读取失败"; }

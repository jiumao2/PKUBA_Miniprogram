import { Button, ScrollView, Text, View } from "@tarojs/components";
import { useDidShow } from "@tarojs/taro";
import { useEffect, useRef, useState } from "react";
import {
  formatOfficialScore,
  type Division,
  type PlayerLeaderboardItem,
  type PublishedGameSummary,
  type TeamLeaderboardItem,
} from "@pkuba/api-client";

import { api } from "../../api";
import { navigateToOnce } from "../../navigation";
import { gameDetailRoute } from "../../routes";
import { usePublicPageShare } from "../../sharing";
import { syncTabBar } from "../../tabbar";
import {
  loadCompleteList,
  PLAYER_PAGE_SIZE,
  playerPageCount,
  playerVisibleTotal,
} from "./pagination";
import { PLAYER_SORTS, TEAM_SORTS } from "./sorts";
import "./index.css";

type Tab = "teams" | "players" | "games";
type Order = "asc" | "desc";

export default function DataPage() {
  const [tab, setTab] = useState<Tab>("teams");
  const [divisions, setDivisions] = useState<Division[]>([]);
  const [divisionId, setDivisionId] = useState("");
  const selectedDivisionId = useRef("");
  const [teamSort, setTeamSort] = useState("points_per_game");
  const [playerSort, setPlayerSort] = useState("points_per_game");
  const [teamOrder, setTeamOrder] = useState<Order>("desc");
  const [playerOrder, setPlayerOrder] = useState<Order>("desc");
  const [teams, setTeams] = useState<TeamLeaderboardItem[]>([]);
  const [players, setPlayers] = useState<PlayerLeaderboardItem[]>([]);
  const [playerPage, setPlayerPage] = useState(1);
  const [playerTotal, setPlayerTotal] = useState(0);
  const [games, setGames] = useState<PublishedGameSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requestVersion = useRef(0);
  const seasonRequestVersion = useRef(0);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const order = tab === "teams" ? teamOrder : playerOrder;

  usePublicPageShare({
    title: "PKUBA 球队与球员数据",
    path: "/pages/data/index",
  });

  useDidShow(() => {
    syncTabBar(3);
    setRefreshVersion((value) => value + 1);
  });
  useEffect(() => {
    const version = ++seasonRequestVersion.current;
    void api.getCurrentSeason().then((season) => {
      if (version !== seasonRequestVersion.current) return;
      setDivisions(season.divisions);
      const initial = season.divisions.find((division) => division.code === "men-a") ?? season.divisions[0];
      const nextDivisionId = season.divisions.some((division) => division.id === selectedDivisionId.current)
        ? selectedDivisionId.current
        : initial?.id || "";
      if (nextDivisionId !== selectedDivisionId.current) {
        requestVersion.current += 1;
        setTeams([]); setPlayers([]); setGames([]);
        setPlayerPage(1); setPlayerTotal(0); setError("");
        setLoading(Boolean(nextDivisionId));
        selectedDivisionId.current = nextDivisionId;
        setDivisionId(nextDivisionId);
      }
      if (!initial) setLoading(false);
    }).catch((reason) => {
      if (version !== seasonRequestVersion.current) return;
      setError(messageOf(reason));
      setLoading(false);
    });
    return () => {
      seasonRequestVersion.current += 1;
    };
  }, [refreshVersion]);
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
  }, [divisionId, order, playerPage, playerSort, refreshVersion, tab, teamSort]);

  const sorts = tab === "teams" ? TEAM_SORTS : PLAYER_SORTS;
  function selectSort(nextSort: string) {
    const current = tab === "teams" ? teamSort : playerSort;
    requestVersion.current += 1;
    if (tab === "teams") setTeams([]); else setPlayers([]);
    setLoading(Boolean(divisionId));
    if (divisionId) setError("");
    setPlayerPage(1);
    if (nextSort === current) {
      if (tab === "teams") {
        setTeamOrder((value) => value === "desc" ? "asc" : "desc");
      } else {
        setPlayerOrder((value) => value === "desc" ? "asc" : "desc");
      }
    }
    else {
      const defaultOrder = sorts.find(([value]) => value === nextSort)?.[2] ?? "desc";
      if (tab === "teams") {
        setTeamSort(nextSort);
        setTeamOrder(defaultOrder);
      } else {
        setPlayerSort(nextSort);
        setPlayerOrder(defaultOrder);
      }
    }
  }
  const hasContent = tab === "teams"
    ? teams.length > 0
    : tab === "players"
      ? players.length > 0
      : games.length > 0;

  return <View className="page data-page">
    <View className="data-sticky">
      <View className="data-tabs">
        {(["teams", "players", "games"] as Tab[]).map((value) => <Button
          className={tab === value ? "data-tab active" : "data-tab"} key={value}
          onClick={() => {
            if (value === tab) return;
            requestVersion.current += 1;
            setTeams([]); setPlayers([]); setGames([]);
            setTab(value); setPlayerPage(1);
            setLoading(Boolean(divisionId));
            if (divisionId) setError("");
          }}
        >{value === "teams" ? "球队" : value === "players" ? "球员" : "单场"}</Button>)}
      </View>
      <ScrollView className="division-strip" scrollX showScrollbar={false}>
        <View className="filter-row">{divisions.map((division) => <Button
          className={divisionId === division.id ? "filter-chip active" : "filter-chip"}
          key={division.id} onClick={() => {
            if (division.id === divisionId) return;
            requestVersion.current += 1;
            setTeams([]); setPlayers([]); setGames([]);
            selectedDivisionId.current = division.id;
            setDivisionId(division.id); setPlayerPage(1); setLoading(true); setError("");
          }}
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
    {loading && !hasContent && <State title="正在读取数据…" />}
    {!loading && !error && divisions.length === 0 && <State title="当前赛季尚未配置组别" />}
    {loading && hasContent && <View className="data-refreshing"><Text>正在更新</Text></View>}
    {error && !hasContent && <State title={error} tone="error" />}
    {error && hasContent && <View className="data-inline-error"><Text>{error}</Text></View>}
    {divisions.length > 0 && (!loading || hasContent) && tab === "teams" && <TeamTable rows={teams} sort={teamSort} />}
    {divisions.length > 0 && (!loading || hasContent) && tab === "players" && <View>
      <PlayerTable rows={players} sort={playerSort} />
      <PlayerPager
        page={playerPage}
        total={playerTotal}
        onPageChange={(page) => {
          requestVersion.current += 1;
          setPlayers([]);
          setLoading(true);
          setError("");
          setPlayerPage(page);
        }}
      />
    </View>}
    {divisions.length > 0 && (!loading || hasContent) && tab === "games" && (
      <GameList games={games} onOpen={(id) => void navigateToOnce(gameDetailRoute(id))} />
    )}
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
      <View className="identity-cell"><Text>{row.player_name}</Text><Text>{row.team_name} · {row.division_name}</Text></View>
      <View className="metric-cell"><Text>{playerMetric(row, sort)}</Text><Text>{playerMetricLabel(sort)}</Text></View>
      <View className="record-cell"><Text>{row.games_played}</Text><Text>首发 {row.starts}</Text></View>
    </View>)}
  </View>;
}

function GameList({ games, onOpen }: { games: PublishedGameSummary[]; onOpen: (id: string) => void }) {
  if (!games.length) return <State title="暂无单场数据" />;
  return <View className="game-list">{games.map((game) => {
    const score = formatOfficialScore(game.home_score, game.away_score);
    return <Button
      className={`game-row ${game.division_gender === "WOMEN" ? "women" : ""}`}
      key={game.game_id} onClick={() => onOpen(game.game_id)}
    ><View><Text>{game.date.slice(5)} · {game.start_time}</Text><Text>{game.division_name}</Text></View>
      <View className="game-matchup"><Text>{game.home_name}</Text>{score && <Text className="game-score">{score}</Text>}<Text>{game.away_name}</Text></View>
      <Text className="chevron">›</Text></Button>;
  })}</View>;
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
  };
  return values[sort];
}
function playerMetricLabel(sort: string) { return PLAYER_SORTS.find(([value]) => value === sort)?.[1] ?? ""; }
function signed(value: number) { return `${value > 0 ? "+" : ""}${value.toFixed(1)}`; }
function messageOf(reason: unknown) { return reason instanceof Error ? reason.message : "数据读取失败"; }

import { ScrollView, Text, View } from "@tarojs/components";
import { useDidShow } from "@tarojs/taro";
import { useState } from "react";
import type {
  DivisionStandings,
  GroupStandings,
  Standings,
  StandingsEntry,
  StandingsMatch,
} from "@pkuba/api-client";

import { api } from "../../api";
import { usePublicPageShare } from "../../sharing";
import { syncTabBar } from "../../tabbar";
import "./index.css";

export default function StandingsPage() {
  const [data, setData] = useState<Standings | null>(null);
  const [divisionId, setDivisionId] = useState("");
  const [groupId, setGroupId] = useState("");
  const [message, setMessage] = useState("正在计算排名…");

  usePublicPageShare({
    title: "PKUBA 赛事排名",
    path: "/pages/standings/index",
  });

  useDidShow(() => {
    syncTabBar(2);
    setMessage("正在计算排名…");
    api.getStandings()
      .then((result) => {
        setData(result);
        const firstDivision = result.divisions[0];
        setDivisionId((current) =>
          result.divisions.some((division) => division.id === current)
            ? current
            : (firstDivision?.id ?? ""),
        );
        setGroupId((current) => {
          const groupStillExists = result.divisions.some((division) =>
            division.groups.some((group) => group.id === current),
          );
          return groupStillExists
            ? current
            : (firstDivision?.groups[0]?.id ?? "");
        });
        setMessage(result.divisions.length ? "" : "当前赛季尚无分组排名。");
      })
      .catch((reason: unknown) => {
        setMessage(reason instanceof Error ? reason.message : "排名读取失败");
      });
  });

  const division = selectedDivision(data, divisionId);
  const group = selectedGroup(division, groupId);

  const selectDivision = (next: DivisionStandings) => {
    setDivisionId(next.id);
    setGroupId(next.groups[0]?.id ?? "");
  };

  return (
    <View className="page standings-page">
      <Text className="page-title">排名</Text>

      {data && data.divisions.length > 0 && (
        <>
          <ScrollView scrollX className="standings-tabs" showScrollbar={false}>
            <View className="standings-tab-row">
              {data.divisions.map((item) => (
                <View
                  className={`standings-tab ${item.id === division?.id ? "is-active" : ""} ${genderClass(item.gender)}`}
                  key={item.id}
                  onClick={() => selectDivision(item)}
                >
                  <Text>{item.name}</Text>
                </View>
              ))}
            </View>
          </ScrollView>

          {division && division.groups.length > 1 && (
            <ScrollView scrollX className="group-tabs" showScrollbar={false}>
              <View className="group-tab-row">
                {division.groups.map((item) => (
                  <View
                    className={`group-tab ${item.id === group?.id ? "is-active" : ""}`}
                    key={item.id}
                    onClick={() => setGroupId(item.id)}
                  >
                    <Text>{item.name}</Text>
                  </View>
                ))}
              </View>
            </ScrollView>
          )}

          {division && group && <GroupTable group={group} gender={division.gender} />}
        </>
      )}

      {message && (
        <View className="state standings-state">
          <Text className="state-detail">{message}</Text>
        </View>
      )}
    </View>
  );
}

function GroupTable({ group, gender }: { group: GroupStandings; gender: string }) {
  if (!group.entries.length) {
    return <View className="state"><Text className="state-detail">本组暂无参赛球队。</Text></View>;
  }
  return (
    <>
      <View className={`ranking-table ${genderClass(gender)}`}>
        <View className="ranking-row ranking-head">
          <Text className="rank-cell">名次</Text>
          <Text className="team-cell">球队</Text>
          <Text>赛</Text>
          <Text>胜</Text>
          <Text>负</Text>
          <Text className="points-cell">积分</Text>
          <Text>净胜</Text>
        </View>
        {group.entries.map((entry) => (
          <View className="ranking-row" key={entry.team_id}>
            <Text className={`rank-cell rank-${entry.rank}`}>{entry.rank}</Text>
            <Text className="team-cell ranking-team">{entry.team_name}</Text>
            <Text>{entry.played}</Text>
            <Text>{entry.wins}</Text>
            <Text>{entry.losses}</Text>
            <Text className="points-cell">{entry.competition_points}</Text>
            <Text>{signed(entry.point_difference)}</Text>
          </View>
        ))}
      </View>

      <View className="matrix-heading">
        <Text className="section-title">交手记录</Text>
      </View>
      <ScrollView scrollX className="matrix-scroll" showScrollbar={false}>
        <View className="result-matrix" style={{ width: `${210 + group.entries.length * 132}rpx` }}>
          <View className="matrix-row matrix-head-row">
            <Text className="matrix-team-cell">球队</Text>
            {group.entries.map((entry) => (
              <Text className="matrix-cell matrix-opponent" key={entry.team_id}>
                {entry.team_name}
              </Text>
            ))}
          </View>
          {group.entries.map((entry) => (
            <View className="matrix-row" key={entry.team_id}>
              <Text className="matrix-team-cell">{entry.team_name}</Text>
              {group.entries.map((opponent) => (
                <MatrixCell
                  entry={entry}
                  opponent={opponent}
                  matches={group.matches}
                  key={opponent.team_id}
                />
              ))}
            </View>
          ))}
        </View>
      </ScrollView>
    </>
  );
}

function MatrixCell({
  entry,
  opponent,
  matches,
}: {
  entry: StandingsEntry;
  opponent: StandingsEntry;
  matches: StandingsMatch[];
}) {
  if (entry.team_id === opponent.team_id) {
    return <View className="matrix-cell self-cell"><Text>—</Text></View>;
  }
  const match = matches.find(
    (item) =>
      (item.home_team_id === entry.team_id && item.away_team_id === opponent.team_id) ||
      (item.away_team_id === entry.team_id && item.home_team_id === opponent.team_id),
  );
  if (!match || match.home_score === null || match.away_score === null) {
    return <View className="matrix-cell"><Text>—</Text></View>;
  }
  const entryIsHome = match.home_team_id === entry.team_id;
  const score = entryIsHome
    ? `${match.home_score}:${match.away_score}`
    : `${match.away_score}:${match.home_score}`;
  const points = entryIsHome
    ? match.home_competition_points
    : match.away_competition_points;
  return (
    <View className="matrix-cell result-cell">
      <Text className="matrix-score">{score}</Text>
      <Text className="matrix-points">{points} 分</Text>
    </View>
  );
}

function selectedDivision(data: Standings | null, divisionId: string) {
  return data?.divisions.find((division) => division.id === divisionId) ?? data?.divisions[0];
}

function selectedGroup(division: DivisionStandings | undefined, groupId: string) {
  return division?.groups.find((group) => group.id === groupId) ?? division?.groups[0];
}

function signed(value: number) {
  return value > 0 ? `+${value}` : String(value);
}

function genderClass(gender: string) {
  return gender === "WOMEN" ? "gender-women" : "gender-men";
}

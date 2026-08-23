import { ScrollView, Text, View } from "@tarojs/components";
import { useDidShow } from "@tarojs/taro";
import { useRef, useState } from "react";
import type { Division, MiniAppMe } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import { ScheduleDayScroller } from "../../components/schedule-day-scroller";
import { navigateToOnce } from "../../navigation";
import "./index.css";

export default function AdminGamesPage() {
  const [me, setMe] = useState<MiniAppMe | null>(null);
  const [divisions, setDivisions] = useState<Division[]>([]);
  const [divisionId, setDivisionId] = useState("all");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  const requestVersionRef = useRef(0);

  useDidShow(() => {
    const token = getMiniAppSession();
    if (!token) {
      setMessage("请先登录管理员账号。");
      setLoading(false);
      return;
    }
    const requestVersion = ++requestVersionRef.current;
    setRefreshKey((value) => value + 1);
    setLoading(true);
    Promise.all([api.getMiniAppMe(token), api.getCurrentSeason()])
      .then(([current, season]) => {
        if (requestVersion !== requestVersionRef.current) return;
        setMe(current);
        setDivisions(season.divisions);
        setMessage(current.admin_role ? "" : "当前账号没有管理员权限。");
      })
      .catch((reason: unknown) => {
        if (requestVersion === requestVersionRef.current) {
          setMessage(reason instanceof Error ? reason.message : "读取失败");
        }
      })
      .finally(() => {
        if (requestVersion === requestVersionRef.current) setLoading(false);
      });
  });

  return (
    <View className="page admin-games-page">
      <Text className="page-title">赛程与资料</Text>
      {me?.admin_role === "SUPERADMIN" && (
        <View className="state"><Text className="state-detail">赛程直接修改统一在网页后台完成；点击比赛可查看和上传资料。</Text></View>
      )}
      {!!divisions.length && (
        <ScrollView scrollX className="admin-division-tabs" showScrollbar={false}>
          <View className="admin-division-row">
            <View className={`admin-division-tab ${divisionId === "all" ? "is-active" : ""}`} onClick={() => setDivisionId("all")}><Text>全部</Text></View>
            {divisions.map((division) => (
              <View
                className={`admin-division-tab ${divisionId === division.id ? "is-active" : ""} ${division.gender === "WOMEN" ? "is-women" : ""}`}
                key={division.id}
                onClick={() => setDivisionId(division.id)}
              >
                <Text>{division.name}</Text>
              </View>
            ))}
          </View>
        </ScrollView>
      )}
      {loading && !me && <View className="admin-games-skeleton"><View /><View /></View>}
      {message && !loading && <View className="state"><Text className="state-detail">{message}</Text></View>}
      {me?.admin_role && !message && (
        <ScheduleDayScroller
          divisionId={divisionId === "all" ? "" : divisionId}
          mode="admin"
          refreshKey={refreshKey}
          onGameClick={(game) => void navigateToOnce(`/pages/game-media/index?id=${game.id}`)}
        />
      )}
    </View>
  );
}

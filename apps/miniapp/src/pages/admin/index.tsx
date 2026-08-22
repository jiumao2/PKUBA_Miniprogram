import { Button, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useState } from "react";
import type { Game, MiniAppMe, RescheduleRequest } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import { GameTimeline } from "../../components/game-timeline";
import "../../role-workspace.css";
import "./index.css";

export default function AdminWorkspacePage() {
  const [me, setMe] = useState<MiniAppMe | null>(null);
  const [games, setGames] = useState<Game[]>([]);
  const [requests, setRequests] = useState<RescheduleRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useDidShow(() => {
    const token = getMiniAppSession();
    if (!token) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    Promise.all([
      api.getMiniAppMe(token),
      api.getGames(),
      api.listRescheduleRequests(token, true),
    ])
      .then(([current, allGames, activeRequests]) => {
        setMe(current);
        setGames(allGames.slice(-10));
        setRequests(activeRequests);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "读取失败"))
      .finally(() => setLoading(false));
  });

  const adminRole = me?.admin_role;
  const waitingAdmin = requests.filter((item) => item.actions.some((action) => action.startsWith("ADMIN_")));
  const copyAdminAddress = async () => {
    await Taro.setClipboardData({ data: PKUBA_API_BASE_URL });
  };

  if (!loading && !getMiniAppSession()) {
    return <View className="page"><Text className="page-title">管理员</Text><View className="state"><Text className="state-detail">请先在“我的”完成微信登录。</Text></View></View>;
  }
  if (!loading && me && !adminRole) {
    return <View className="page"><Text className="page-title">管理员</Text><View className="state"><Text className="state-detail">当前账号没有管理员权限。</Text></View></View>;
  }
  return (
    <View className="page admin-workspace-page">
      <Text className="page-title">管理员工作台</Text>
      {loading && <View className="state"><Text className="state-detail">正在读取待办…</Text></View>}
      {adminRole && (
        <>
          <View className="workspace-identity admin-identity">
            <Text className="workspace-role">{adminRole === "SUPERADMIN" ? "超级管理员" : "普通管理员"}</Text>
            <Text className="workspace-name">{me?.account.username}</Text>
          </View>
          <View className="workspace-actions">
            <Button
              className="workspace-action primary"
              onClick={() => Taro.navigateTo({ url: "/scoresheet/pages/list/index" })}
            >
              记录表核对
              <Text className="workspace-count">跨端同步 · 识别发布</Text>
            </Button>
            {adminRole === "SUPERADMIN" && (
              <Button
                className="workspace-action"
                onClick={() => Taro.navigateTo({ url: "/pages/reschedule-requests/index" })}
              >
                调赛处理
                <Text className="workspace-count">{waitingAdmin.length} 项待处理</Text>
              </Button>
            )}
            <Button
              className="workspace-action"
              onClick={() => Taro.navigateTo({ url: "/pages/admin-games/index" })}
            >
              赛程与资料
              <Text className="workspace-count">{adminRole === "SUPERADMIN" ? "网页后台编辑" : "查看赛程"}</Text>
            </Button>
            <Button className="workspace-action wide" onClick={() => void copyAdminAddress()}>
              复制网页后台地址
            </Button>
          </View>

          <View className="workspace-section-heading">
            <Text className="workspace-section-title">近期赛程</Text>
            <Text className="workspace-section-count">{games.length} 场</Text>
          </View>
          {games.length ? (
            <GameTimeline
              games={games}
              onGameClick={(game) => Taro.navigateTo({ url: `/pages/game-media/index?id=${game.id}` })}
            />
          ) : (
            <View className="state"><Text className="state-detail">当前赛季暂无赛程。</Text></View>
          )}
        </>
      )}
      {error && <View className="flow-feedback">{error}</View>}
    </View>
  );
}

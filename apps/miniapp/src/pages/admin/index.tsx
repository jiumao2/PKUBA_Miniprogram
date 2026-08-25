import { Button, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useRef, useState } from "react";
import type { MobileAdminDashboard } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import { GameTimeline } from "../../components/game-timeline";
import { navigateToOnce } from "../../navigation";
import { gameDetailRoute } from "../../routes";
import "../../role-workspace.css";
import "./index.css";

export default function AdminWorkspacePage() {
  const [dashboard, setDashboard] = useState<MobileAdminDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const requestVersionRef = useRef(0);

  useDidShow(() => {
    const token = getMiniAppSession();
    if (!token) {
      setLoading(false);
      return;
    }
    const requestVersion = ++requestVersionRef.current;
    if (dashboard) setRefreshing(true);
    else setLoading(true);
    setError("");
    api.getMobileAdminDashboard(token)
      .then((result) => {
        if (requestVersion !== requestVersionRef.current) return;
        setDashboard(result);
      })
      .catch((reason: unknown) => {
        if (requestVersion === requestVersionRef.current) {
          setError(reason instanceof Error ? reason.message : "读取失败");
        }
      })
      .finally(() => {
        if (requestVersion !== requestVersionRef.current) return;
        setLoading(false);
        setRefreshing(false);
      });
  });

  const adminRole = dashboard?.admin_role;
  const copyAdminAddress = async () => {
    await Taro.setClipboardData({ data: PKUBA_ADMIN_WEB_URL });
  };

  if (!loading && !getMiniAppSession()) {
    return <View className="page"><Text className="page-title">管理员</Text><View className="state"><Text className="state-detail">请先在“我的”完成微信登录。</Text></View></View>;
  }
  return (
    <View className="page admin-workspace-page">
      <Text className="page-title">管理员工作台</Text>
      {loading && !dashboard && <AdminWorkspaceSkeleton />}
      {refreshing && <View className="workspace-refreshing"><Text>正在更新</Text></View>}
      {adminRole && (
        <>
          <View className="workspace-identity admin-identity">
            <Text className="workspace-role">{adminRole === "SUPERADMIN" ? "超级管理员" : "普通管理员"}</Text>
            <Text className="workspace-name">{dashboard.username}</Text>
          </View>
          <View className="workspace-actions">
            <Button
              className="workspace-action primary"
              onClick={() => void navigateToOnce("/scoresheet/pages/list/index")}
            >
              记录表核对
              <Text className="workspace-count">跨端同步 · 识别发布</Text>
            </Button>
            {adminRole === "SUPERADMIN" && (
              <Button
                className="workspace-action"
                onClick={() => void navigateToOnce("/pages/reschedule-requests/index")}
              >
                调赛处理
                <Text className="workspace-count">{dashboard.active_reschedule_count} 项待处理</Text>
              </Button>
            )}
            <Button
              className="workspace-action"
              onClick={() => void navigateToOnce("/pages/admin-games/index")}
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
            <Text className="workspace-section-count">{dashboard.recent_games.length} 场</Text>
          </View>
          {dashboard.recent_games.length ? (
            <GameTimeline
              games={dashboard.recent_games}
              onGameClick={(game) => void navigateToOnce(gameDetailRoute(game.id))}
            />
          ) : (
            <View className="state"><Text className="state-detail">当前赛季暂无赛程。</Text></View>
          )}
        </>
      )}
      {error && <View className={dashboard ? "workspace-inline-error" : "flow-feedback"}>{error}</View>}
    </View>
  );
}

function AdminWorkspaceSkeleton() {
  return <View className="workspace-skeleton" aria-label="正在读取管理员工作台">
    <View className="skeleton-identity" />
    <View className="skeleton-actions"><View /><View /><View /></View>
    <View className="skeleton-game" />
  </View>;
}

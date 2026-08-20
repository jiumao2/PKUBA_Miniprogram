import { Button, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useState } from "react";
import type { MiniAppMe } from "@pkuba/api-client";

import { api } from "../../api";
import {
  clearMiniAppSession,
  exchangeCurrentWeChat,
  getMiniAppSession,
} from "../../auth";
import { syncTabBar } from "../../tabbar";
import "./index.css";

export default function MinePage() {
  const [me, setMe] = useState<MiniAppMe | null>(null);
  const [loading, setLoading] = useState(true);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const identify = async () => {
    setLoading(true);
    setError(null);
    try {
      const savedToken = getMiniAppSession();
      if (!savedToken) {
        const exchanged = await exchangeCurrentWeChat();
        setMe(exchanged.requires_profile ? null : exchanged.me);
        return;
      }
      try {
        setMe(await api.getMiniAppMe(savedToken));
      } catch {
        clearMiniAppSession();
        const exchanged = await exchangeCurrentWeChat();
        setMe(exchanged.requires_profile ? null : exchanged.me);
      }
    } catch (reason: unknown) {
      clearMiniAppSession();
      setMe(null);
      setError(reason instanceof Error ? reason.message : "登录状态读取失败");
    } finally {
      setLoading(false);
    }
  };

  useDidShow(() => {
    syncTabBar(4);
    void identify();
  });

  const logout = async () => {
    const token = getMiniAppSession();
    setLogoutBusy(true);
    setError(null);
    try {
      if (token) await api.logoutMiniApp(token);
      Taro.showToast({ title: "已退出", icon: "success" });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "服务端退出失败");
    } finally {
      clearMiniAppSession();
      setMe(null);
      setLogoutBusy(false);
    }
  };

  return (
    <View className="page mine-page">
      <Text className="page-title">我的</Text>

      {loading && <State title="正在识别微信身份" />}
      {!loading && !me && (
        <View className="account-section login-entry-section">
          <Text className="section-title">微信登录</Text>
          <Text className="section-detail">首次使用需要设置唯一昵称。</Text>
          <Button
            className="role-entry role-entry-account"
            onClick={() => Taro.navigateTo({ url: "/pages/auth/index" })}
          >
            微信登录
          </Button>
        </View>
      )}
      {!loading && me && (
        <>
          <View className="account-summary">
            <View>
              <Text className="summary-label">当前账号</Text>
              <Text className="summary-name">{me.account.username}</Text>
            </View>
          </View>

          <View className="account-section">
            <Text className="section-title">领队</Text>
            {me.leader_binding ? (
              <>
                <View className={`role-line ${genderClass(me.leader_binding.division_gender)}`}>
                  <View>
                    <Text className="role-name">{me.leader_binding.team_name}</Text>
                    <Text className="role-meta">{me.leader_binding.division_name}</Text>
                  </View>
                </View>
                <Button className="role-workspace-link" onClick={() => Taro.navigateTo({ url: "/pages/leader/index" })}>
                  进入领队工作台
                </Button>
              </>
            ) : (
              <>
                <Text className="section-detail">每个赛季一人只能认领一队，认领后仅超级管理员可纠正。</Text>
                <Button
                  className="secondary-action"
                  onClick={() => Taro.navigateTo({ url: "/pages/leader-register/index" })}
                >
                  认领球队
                </Button>
              </>
            )}
          </View>

          <View className="account-section">
            <Text className="section-title">管理员</Text>
            {me.admin_role ? (
              <>
                <View className="admin-role-line">
                  <View>
                    <Text className="role-name">{me.admin_role === "SUPERADMIN" ? "超级管理员" : "普通管理员"}</Text>
                    <Text className="role-meta">网页登录账号：{me.account.username}</Text>
                  </View>
                </View>
                <Button className="role-workspace-link" onClick={() => Taro.navigateTo({ url: "/pages/admin/index" })}>
                  进入管理员工作台
                </Button>
              </>
            ) : (
              <>
                <Text className="section-detail">邀请码属于当前赛季；初始网页登录密码与邀请码相同，可在后台修改。</Text>
                <Button
                  className="secondary-action"
                  onClick={() => Taro.navigateTo({ url: "/pages/admin-register/index" })}
                >
                  注册管理员
                </Button>
              </>
            )}
          </View>
          <Button
            className="logout-action"
            disabled={logoutBusy}
            onClick={() => void logout()}
          >
            {logoutBusy ? "正在退出…" : "退出当前账号"}
          </Button>
        </>
      )}

      {error && <View className="feedback error-feedback">{error}</View>}
      {!loading && error && !me && (
        <Button className="secondary-action retry-action" onClick={() => void identify()}>重新识别</Button>
      )}
    </View>
  );
}

function genderClass(gender: string) {
  return gender === "WOMEN" ? "gender-women" : "gender-men";
}

function State({ title, detail }: { title: string; detail?: string }) {
  return <View className="state"><Text className="state-title">{title}</Text>{detail && <Text className="state-detail">{detail}</Text>}</View>;
}

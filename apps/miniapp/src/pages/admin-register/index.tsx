import { Button, Input, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useState } from "react";
import type { MiniAppMe, Season } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import "../../auth-pages.css";

export default function AdminRegisterPage() {
  const [season, setSeason] = useState<Season | null>(null);
  const [me, setMe] = useState<MiniAppMe | null>(null);
  const [inviteCode, setInviteCode] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useDidShow(() => {
    const token = getMiniAppSession();
    if (!token) {
      setLoading(false);
      return;
    }
    Promise.all([api.getCurrentSeason(), api.getMiniAppMe(token)])
      .then(([currentSeason, currentMe]) => {
        setSeason(currentSeason);
        setMe(currentMe);
      })
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "读取账号失败"))
      .finally(() => setLoading(false));
  });

  const register = async () => {
    const token = getMiniAppSession();
    if (!season || !token) return;
    if (!inviteCode.trim()) return setError("请填写当前赛季邀请码。");
    setBusy(true);
    setError(null);
    try {
      const updated = await api.registerAdmin({
        season_id: season.id,
        invite_code: inviteCode.trim(),
      }, token);
      setMe(updated);
      setInviteCode("");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "管理员注册失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <View className="page auth-flow-page">
      <Text className="auth-title">注册管理员</Text>
      <Text className="auth-intro">填写当前赛季邀请码；首次网页登录密码与邀请码相同。</Text>

      {loading && <View className="auth-panel"><Text className="auth-detail">正在读取账号…</Text></View>}
      {!loading && !getMiniAppSession() && (
        <View className="auth-panel">
          <Text className="auth-panel-title">请先登录</Text>
          <Button className="auth-primary" onClick={() => Taro.redirectTo({ url: "/pages/auth/index?intent=admin" })}>微信登录</Button>
        </View>
      )}
      {!loading && me?.admin_role && (
        <View className="auth-panel auth-success-panel">
          <Text className="auth-panel-title">管理员身份已生效</Text>
          <Text className="auth-detail">网页登录名：{me.account.username}</Text>
          <Text className="auth-detail">请登录管理网站后，在“修改密码”中设置个人密码。</Text>
          <Button className="auth-secondary" onClick={() => Taro.switchTab({ url: "/pages/mine/index" })}>返回我的</Button>
        </View>
      )}
      {!loading && me && !me.admin_role && (
        <View className="auth-panel">
          <Text className="auth-panel-title">填写注册信息</Text>
          <Text className="auth-detail">网页登录名将使用当前昵称：{me.account.username}</Text>
          <Input className="auth-field" password placeholder="当前赛季邀请码" value={inviteCode} onInput={(event) => setInviteCode(event.detail.value)} />
          <Button className="auth-primary" disabled={busy} onClick={() => void register()}>
            {busy ? "正在注册…" : "确认注册"}
          </Button>
        </View>
      )}
      {error && <View className="auth-feedback auth-error">{error}</View>}
    </View>
  );
}

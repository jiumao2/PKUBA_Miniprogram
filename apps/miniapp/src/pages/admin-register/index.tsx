import { Button, Input, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useState } from "react";
import type { MiniAppMe } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import "../../auth-pages.css";
import { passwordCharacterCount, validateAdminRegistration } from "./validation";

export default function AdminRegisterPage() {
  const [me, setMe] = useState<MiniAppMe | null>(null);
  const [inviteCode, setInviteCode] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showPasswordConfirmation, setShowPasswordConfirmation] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useDidShow(() => {
    const token = getMiniAppSession();
    if (!token) {
      setLoading(false);
      return;
    }
    api.getMiniAppMe(token)
      .then(setMe)
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "读取账号失败"))
      .finally(() => setLoading(false));
  });

  const register = async () => {
    const token = getMiniAppSession();
    if (!token) return;
    const validationError = validateAdminRegistration(inviteCode, password, passwordConfirmation);
    if (validationError) return setError(validationError);
    setBusy(true);
    setError(null);
    try {
      const updated = await api.registerAdmin({
        invite_code: inviteCode.trim(),
        password,
      }, token);
      setMe(updated);
      setInviteCode("");
      setPassword("");
      setPasswordConfirmation("");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "管理员注册失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <View className="page auth-flow-page">
      <Text className="auth-title">注册管理员</Text>
      <Text className="auth-intro">填写管理员邀请码，并设置个人网页登录密码。</Text>

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
          <Text className="auth-detail">可使用注册时设置的密码直接登录管理网站。</Text>
          <Button className="auth-secondary" onClick={() => Taro.switchTab({ url: "/pages/mine/index" })}>返回我的</Button>
        </View>
      )}
      {!loading && me && !me.admin_role && (
        <View className="auth-panel">
          <Text className="auth-panel-title">填写注册信息</Text>
          <Text className="auth-detail">网页登录名将使用当前昵称：{me.account.username}</Text>
          <Input
            className="auth-field"
            password
            placeholder="管理员邀请码"
            value={inviteCode}
            onInput={(event) => {
              setInviteCode(event.detail.value);
              setError(null);
            }}
          />
          <View className="auth-password-row">
            <Input
              className="auth-field auth-password-input"
              password={!showPassword}
              placeholder="设置网页密码（至少 4 个字符）"
              value={password}
              onInput={(event) => {
                setPassword(event.detail.value);
                setError(null);
              }}
            />
            <Text
              className="auth-password-toggle"
              onClick={() => setShowPassword((value) => !value)}
            >
              {showPassword ? "隐藏" : "显示"}
            </Text>
          </View>
          {password && passwordCharacterCount(password) < 4 && (
            <Text className="auth-field-error">网页密码至少需要 4 个字符。</Text>
          )}
          <View className="auth-password-row">
            <Input
              className="auth-field auth-password-input"
              password={!showPasswordConfirmation}
              placeholder="再次输入网页密码"
              value={passwordConfirmation}
              onInput={(event) => {
                setPasswordConfirmation(event.detail.value);
                setError(null);
              }}
            />
            <Text
              className="auth-password-toggle"
              onClick={() => setShowPasswordConfirmation((value) => !value)}
            >
              {showPasswordConfirmation ? "隐藏" : "显示"}
            </Text>
          </View>
          {passwordConfirmation && password !== passwordConfirmation && (
            <Text className="auth-field-error">两次输入的网页密码不一致。</Text>
          )}
          <Button className="auth-primary" disabled={busy} onClick={() => void register()}>
            {busy ? "正在注册…" : "确认注册"}
          </Button>
        </View>
      )}
      {error && <View className="auth-feedback auth-error">{error}</View>}
    </View>
  );
}

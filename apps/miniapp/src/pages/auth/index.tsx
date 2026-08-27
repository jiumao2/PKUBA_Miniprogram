import { Button, Input, Text, View } from "@tarojs/components";
import Taro from "@tarojs/taro";
import { useEffect, useRef, useState } from "react";
import type { MiniAppMe } from "@pkuba/api-client";

import { api } from "../../api";
import { exchangeCurrentWeChat, saveMiniAppSession } from "../../auth";
import { authReturnUrl } from "../../authReturn";
import "../../auth-pages.css";

type Intent = "leader" | "admin" | "account";

export default function AuthPage() {
  const params = Taro.getCurrentInstance().router?.params ?? {};
  const rawIntent = params.intent;
  const returnUrl = authReturnUrl(params);
  const intent: Intent = rawIntent === "leader" || rawIntent === "admin" ? rawIntent : "account";
  const [profileTicket, setProfileTicket] = useState("");
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestSequence = useRef(0);
  const pending = useRef(false);
  useEffect(() => () => { requestSequence.current += 1; }, []);

  const login = async () => {
    if (pending.current) return;
    pending.current = true;
    const sequence = ++requestSequence.current;
    setBusy(true);
    setError(null);
    try {
      const exchanged = await exchangeCurrentWeChat();
      if (sequence !== requestSequence.current) return;
      if (exchanged.requires_profile) {
        setProfileTicket(exchanged.profile_ticket ?? "");
      } else if (exchanged.me) {
        await continueForRole(exchanged.me, intent, returnUrl);
      }
    } catch (reason: unknown) {
      if (sequence === requestSequence.current) setError(reason instanceof Error ? reason.message : "微信登录失败");
    } finally {
      if (sequence === requestSequence.current) { pending.current = false; setBusy(false); }
    }
  };

  const completeProfile = async () => {
    if (pending.current) return;
    if (!username.trim()) return setError("请设置唯一昵称。");
    pending.current = true;
    const sequence = ++requestSequence.current;
    setBusy(true);
    setError(null);
    try {
      const result = await api.completeProfile(profileTicket, username.trim());
      if (sequence !== requestSequence.current) return;
      saveMiniAppSession(result.session_token);
      await continueForRole(result.me, intent, returnUrl);
    } catch (reason: unknown) {
      if (sequence === requestSequence.current) setError(reason instanceof Error ? reason.message : "昵称注册失败");
    } finally {
      if (sequence === requestSequence.current) { pending.current = false; setBusy(false); }
    }
  };

  return (
    <View className="page auth-flow-page">
      <Text className="auth-title">{profileTicket ? "设置昵称" : "微信登录"}</Text>

      {!profileTicket ? (
        <View className="auth-panel">
          <Button className="auth-primary" disabled={busy} onClick={() => void login()}>
            {busy ? "正在登录…" : "微信登录"}
          </Button>
        </View>
      ) : (
        <View className="auth-panel">
          <Text className="auth-panel-title">注册唯一昵称</Text>
          <Text className="auth-detail">昵称也是管理员网页登录名，当前版本不支持自行修改。</Text>
          <Input
            className="auth-field"
            maxlength={32}
            placeholder="2–32 个字符"
            value={username}
            onInput={(event) => setUsername(event.detail.value)}
          />
          <Button className="auth-primary" disabled={busy} onClick={() => void completeProfile()}>
            {busy ? "正在注册…" : "注册并继续"}
          </Button>
        </View>
      )}
      {error && <View className="auth-feedback auth-error">{error}</View>}
    </View>
  );
}

async function continueForRole(me: MiniAppMe, intent: Intent, returnUrl: string | null) {
  if (intent === "leader" && !me.leader_binding) {
    await Taro.redirectTo({ url: "/pages/leader-register/index" });
    return;
  }
  if (intent === "admin" && !me.admin_role) {
    await Taro.redirectTo({ url: "/pages/admin-register/index" });
    return;
  }
  if (returnUrl) await Taro.redirectTo({ url: returnUrl });
  else await Taro.switchTab({ url: "/pages/mine/index" });
}

import { Button, Text, View } from "@tarojs/components";
import Taro, { useDidHide, useDidShow } from "@tarojs/taro";
import { useEffect, useRef, useState } from "react";
import { createIdempotencyKey, type NotificationChoice, type NotificationConfig } from "@pkuba/api-client";
import { api } from "../api";
import { getMiniAppSession } from "../auth";
import "./notification-subscribe.css";

export function NotificationSubscribe() {
  const [config, setConfig] = useState<NotificationConfig>();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ token: string; payload: NotificationChoice; key: string }>();
  const generation = useRef(0);
  const running = useRef(false);
  const syncing = useRef(false);
  const save = async (command: { token: string; payload: NotificationChoice; key: string }) => {
    if (syncing.current || command.token !== getMiniAppSession()) return;
    syncing.current = true;
    const seq = generation.current;
    setPending(command);
    try {
      await api.saveNotificationChoice(command.payload, command.token, command.key);
      if (command.token !== getMiniAppSession() || seq !== generation.current) return;
      setPending(undefined);
      setMessage(command.payload.choice === "accept" ? "已记录订阅选择，实际发送以微信可用权限为准。消息也会保留在任务箱。" : "已记录选择，消息仍会保留在任务箱。");
    } catch {
      if (command.token === getMiniAppSession() && seq === generation.current) setMessage("订阅选择尚未同步，请点击核对；无需再次弹出授权。");
    } finally {
      syncing.current = false;
    }
  };
  const refresh = () => {
    const token = getMiniAppSession(), seq = ++generation.current;
    setConfig(undefined); setMessage("");
    if (pending && pending.token !== token) setPending(undefined);
    if (!token) return;
    void api.getNotificationConfig(token).then((next) => {
      if (seq === generation.current && token === getMiniAppSession()) setConfig(next);
    }).catch(() => { if (seq === generation.current) setMessage("微信提醒设置暂时无法读取，任务箱仍可使用。"); });
  };
  useDidShow(refresh);
  useDidHide(() => { generation.current += 1; setConfig(undefined); setMessage(""); });
  useEffect(() => {
    // This component can mount after the page's show event when private data finishes loading.
    refresh();
    return () => { generation.current += 1; };
  }, []);
  const subscribe = async (templateId: string) => {
    const token = getMiniAppSession();
    if (!token || running.current || pending) return;
    running.current = true; setBusy(true); setMessage("");
    try {
      // Called directly from the user's button gesture, using already loaded template IDs.
      // Pinned Taro types also require Alipay-only entityIds; the WeChat API takes tmplIds.
      const request = Taro.requestSubscribeMessage as unknown as (options: { tmplIds: string[] }) => Promise<Record<string, unknown>>;
      const result = await request({ tmplIds: [templateId] });
      if (token !== getMiniAppSession()) return;
      const choice = result[templateId];
      if (!["accept", "reject", "ban", "filter"].includes(String(choice))) {
        setMessage("微信没有返回有效的订阅选择，请稍后重试。"); return;
      }
      await save({ token, payload: { template_id: templateId, choice: choice as NotificationChoice["choice"] }, key: createIdempotencyKey() });
    } catch { if (token === getMiniAppSession()) setMessage("未完成微信订阅，报名和任务箱不受影响。"); }
    finally { running.current = false; setBusy(false); }
  };
  return <View className="subscribe-panel"><Text>微信服务通知</Text>
    {config?.templates.length === 0 && <Text>订阅模板尚未配置，所有相关消息仍保留在任务箱。</Text>}
    {config?.templates.map((t) => <Button key={t.key} disabled={busy || Boolean(pending)} onClick={() => void subscribe(t.template_id)}>接收{t.name}</Button>)}
    {pending && pending.token === getMiniAppSession() && <Button disabled={busy} onClick={() => void save(pending)}>核对订阅选择</Button>}
    {message && <Text>{message}</Text>}
  </View>;
}

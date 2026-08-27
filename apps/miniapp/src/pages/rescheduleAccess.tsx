import { Button, Text, View } from "@tarojs/components";
import Taro from "@tarojs/taro";
import { useRef } from "react";

import { rescheduleLoginUrl, type AuthReturnEntry } from "../authReturn";

export type RescheduleAccessProblem = {
  kind: "login" | "expired" | "forbidden" | "error";
  message: string;
};

export const RESCHEDULE_LOGIN_REQUIRED: RescheduleAccessProblem = {
  kind: "login",
  message: "请先登录，登录后将返回当前调赛页面。",
};

export function rescheduleAccessProblem(reason: unknown, fallback: string): RescheduleAccessProblem {
  const status = reason !== null && typeof reason === "object" && "status" in reason
    ? reason.status
    : undefined;
  if (status === 401) return { kind: "expired", message: "登录状态已失效，请重新登录后继续。" };
  if (status === 403) return { kind: "forbidden", message: "当前账号没有此操作权限，请前往“我的”核对身份。" };
  return { kind: "error", message: reason instanceof Error ? reason.message : fallback };
}

export function RescheduleAccessNotice({ problem, onRetry, returnEntry, requestId }: {
  problem: RescheduleAccessProblem;
  onRetry: () => void;
  returnEntry: AuthReturnEntry;
  requestId?: string;
}) {
  const navigating = useRef(false);
  const openIdentity = async () => {
    if (navigating.current) return;
    navigating.current = true;
    try {
      if (problem.kind === "forbidden") await Taro.switchTab({ url: "/pages/mine/index" });
      else await Taro.navigateTo({ url: rescheduleLoginUrl(returnEntry, requestId) });
    } catch {
      void Taro.showToast({ title: "页面打开失败，请重试", icon: "none" });
    } finally {
      navigating.current = false;
    }
  };
  return (
    <View className="state">
      <Text className="state-detail">{problem.message}</Text>
      <Button className="flow-secondary" onClick={problem.kind === "error" ? onRetry : () => void openIdentity()}>
        {problem.kind === "error" ? "重新加载" : problem.kind === "forbidden" ? "前往我的" : "登录并继续"}
      </Button>
    </View>
  );
}

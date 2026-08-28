import { Button, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useEffect, useRef, useState } from "react";
import type { InboxTask } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import "./index.css";

type TaskStatus = "OPEN" | "CLOSED";

export default function InboxPage() {
  const [status, setStatus] = useState<TaskStatus>("OPEN");
  const [items, setItems] = useState<InboxTask[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const requestSequence = useRef(0);
  const loadedIdentity = useRef<string | null>(null);

  const load = async (targetStatus: TaskStatus, append = false) => {
    const sequence = ++requestSequence.current;
    const token = getMiniAppSession();
    if (!token) {
      loadedIdentity.current = null;
      setItems([]);
      setNextCursor(null);
      setLoading(false);
      setLoadingMore(false);
      setError("请先在“我的”完成微信登录。");
      return;
    }
    const current = () => sequence === requestSequence.current && token === getMiniAppSession();
    if (loadedIdentity.current !== token) {
      setItems([]);
      setNextCursor(null);
      loadedIdentity.current = token;
      append = false;
    }
    if (append) setLoadingMore(true);
    else { setLoading(true); setLoadingMore(false); }
    setError("");
    try {
      const page = await api.listInbox(
        token,
        targetStatus,
        append ? nextCursor ?? "" : "",
      );
      if (!current()) return;
      setItems((current) => append ? [...current, ...page.items] : page.items);
      setNextCursor(page.next_cursor);
    } catch (reason: unknown) {
      if (current()) setError(reason instanceof Error ? reason.message : "任务读取失败");
    } finally {
      if (current()) { setLoading(false); setLoadingMore(false); }
    }
  };

  useDidShow(() => {
    void load(status);
  });
  useEffect(() => () => { requestSequence.current += 1; }, []);

  const switchStatus = (next: TaskStatus) => {
    if (next === status) return;
    setStatus(next);
    setItems([]);
    setNextCursor(null);
    void load(next);
  };

  const openTask = async (task: InboxTask) => {
    const token = getMiniAppSession();
    if (!token) return;
    setError("");
    try {
      const viewed = await api.viewInboxTask(task.id, token);
      setItems((current) => current.map((item) => item.id === viewed.id ? viewed : item));
      await Taro.navigateTo({ url: viewed.target_url });
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "任务打开失败");
    }
  };

  return (
    <View className="page inbox-page">
      <View className="inbox-heading">
        <Text className="page-title">任务箱</Text>
        <Text className="inbox-count">
          {status === "OPEN" ? `${items.length}${nextCursor ? "+" : ""} 项待处理` : "处理记录"}
        </Text>
      </View>

      <View className="inbox-tabs">
        <View className={`inbox-tab ${status === "OPEN" ? "is-active" : ""}`} onClick={() => switchStatus("OPEN")}>
          <Text>待处理</Text>
        </View>
        <View className={`inbox-tab ${status === "CLOSED" ? "is-active" : ""}`} onClick={() => switchStatus("CLOSED")}>
          <Text>已完成</Text>
        </View>
      </View>

      {loading && <TaskState text="正在读取任务…" />}
      {!loading && !items.length && !error && (
        <TaskState text={status === "OPEN" ? "当前没有待处理任务。" : "还没有已完成任务。"} />
      )}

      {!loading && !!items.length && (
        <View className="task-list">
          {items.map((task) => (
            <View
              className={`task-row ${task.read_at ? "is-viewed" : "is-new"}`}
              key={task.id}
              onClick={() => void openTask(task)}
            >
              <View className="task-main">
                <View className="task-title-line">
                  {!task.read_at && <View className="task-new-dot" />}
                  <Text className="task-title">{task.title}</Text>
                </View>
                {!!task.body && <Text className="task-body">{task.body}</Text>}
                <View className="task-meta-line">
                  <Text className="task-time">{formatDateTime(task.created_at)}</Text>
                  {task.due_at && task.status === "OPEN" && (
                    <Text className="task-due">截止 {formatDateTime(task.due_at)}</Text>
                  )}
                  {task.status === "CLOSED" && <Text className="task-closed">已完成</Text>}
                </View>
              </View>
              <Text className="task-arrow">›</Text>
            </View>
          ))}
        </View>
      )}

      {nextCursor && (
        <Button className="load-more" disabled={loading || loadingMore} onClick={() => void load(status, true)}>
          {loadingMore ? "正在加载…" : "加载更多"}
        </Button>
      )}
      {error && <View className="inbox-error"><Text>{error}</Text></View>}
    </View>
  );
}

function TaskState({ text }: { text: string }) {
  return <View className="task-state"><Text>{text}</Text></View>;
}

function formatDateTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

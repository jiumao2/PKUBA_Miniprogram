import { Button, Input, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { useEffect, useRef, useState } from "react";
import type { ScoresheetQueueItem, ScoresheetQueueScope } from "@pkuba/api-client";

import { api, uploadGameMedia } from "../../../api";
import { getMiniAppSession } from "../../../auth";
import "./index.css";

const STATUS: Record<string, string> = {
  NO_SOURCE: "待上传",
  RECOGNITION_QUEUED: "等待识别",
  RECOGNIZING: "识别中",
  RETRY_WAIT: "等待重试",
  DRAFT: "人工核对",
  RECOGNITION_FAILED: "识别失败",
  READY: "可以发布",
  PUBLISHED: "已发布",
};

export default function ScoresheetListPage() {
  const [items, setItems] = useState<ScoresheetQueueItem[]>([]);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<ScoresheetQueueScope>("ACTION_REQUIRED");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState("");
  const [message, setMessage] = useState("");
  const [adminRole, setAdminRole] = useState("");
  const [, tick] = useState(0);
  const [refreshGeneration, setRefreshGeneration] = useState(0);
  const requestGeneration = useRef(0);
  const pageSize = 20;

  const load = async (silent = false) => {
    const generation = ++requestGeneration.current;
    const token = getMiniAppSession();
    if (!token) {
      if (generation === requestGeneration.current) setLoading(false);
      setMessage("请先登录管理员账号。");
      return;
    }
    if (!silent) setLoading(true);
    try {
      const me = await api.getMiniAppMe(token);
      if (!me.admin_role) {
        setMessage("当前账号没有管理员权限。");
        setItems([]);
        return;
      }
      setAdminRole(me.admin_role);
      const result = await api.getScoresheetQueuePage(token, {
        scope,
        query,
        page,
        pageSize,
      });
      if (generation !== requestGeneration.current) return;
      setItems(result.items);
      setTotal(result.total);
      setMessage("");
    } catch (reason) {
      if (generation !== requestGeneration.current) return;
      setMessage(reason instanceof Error ? reason.message : "记录表队列读取失败");
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  };

  useDidShow(() => setRefreshGeneration((value) => value + 1));
  useEffect(() => {
    const delay = setTimeout(() => void load(), query ? 250 : 0);
    return () => clearTimeout(delay);
  }, [page, query, refreshGeneration, scope]);
  useEffect(() => {
    const poll = setInterval(() => void load(true), 30000);
    const clock = setInterval(() => tick((value) => value + 1), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(clock);
    };
  }, [page, query, scope]);

  const upload = async (item: ScoresheetQueueItem) => {
    const token = getMiniAppSession();
    if (!token) return;
    try {
      const selected = await Taro.chooseMedia({
        count: 1,
        mediaType: ["image"],
        sourceType: ["album", "camera"],
        sizeType: ["original"],
      });
      const file = selected.tempFiles[0];
      if (!file) return;
      const confirm = await Taro.showModal({
        title: "上传唯一记录表原图",
        content: "确认照片包含完整单页记录表、没有缺角或遮挡。上传后将立即进入自动识别。",
        confirmText: "确认上传",
      });
      if (!confirm.confirm) return;
      setUploading(item.game_id);
      await uploadGameMedia(item.game_id, file.tempFilePath, "SCORESHEET", true, token);
      const refreshed = await api.getScoresheetQueuePage(token, {
        scope: "ALL",
        query: item.game_code,
        page: 1,
        pageSize: 5,
      });
      const next = refreshed.items.find((row) => row.game_id === item.game_id);
      if (next?.scoresheet_id) {
        await Taro.navigateTo({ url: `/scoresheet/pages/editor/index?id=${next.scoresheet_id}` });
      }
    } catch (reason) {
      const text = reason instanceof Error ? reason.message : "上传失败";
      if (!text.includes("cancel")) setMessage(text);
    } finally {
      setUploading("");
    }
  };

  return (
    <View className="sheet-list-page">
      <View className="sheet-list-heading">
        <Text className="sheet-list-title">记录表核对</Text>
      </View>
      <Input
        className="sheet-list-search"
        onInput={(event) => {
          setPage(1);
          setQuery(event.detail.value);
        }}
        placeholder="搜索场次或球队"
        value={query}
      />
      <View className="sheet-list-filters" aria-label="记录表状态筛选">
        {([
          ["ACTION_REQUIRED", "待处理"],
          ["IN_PROGRESS", "识别中"],
          ["PUBLISHED", "已发布"],
          ["ALL", "全部"],
        ] as Array<[ScoresheetQueueScope, string]>).map(([value, label]) => (
          <Button
            className={`sheet-list-filter ${scope === value ? "is-active" : ""}`}
            key={value}
            onClick={() => {
              setPage(1);
              setScope(value);
            }}
          >
            {label}
          </Button>
        ))}
      </View>
      {loading && <View className="sheet-list-state">正在读取…</View>}
      {message && <View className="sheet-list-message">{message}</View>}
      <View className="sheet-list-items">
        {items.map((item) => (
          <View className="sheet-list-item" key={item.game_id}>
            <View className="sheet-list-main">
              <View className="sheet-list-meta">
                <Text className="sheet-list-date">
                  {item.date} · {item.start_time}
                </Text>
                <Text className={`sheet-list-status status-${item.status.toLowerCase()}`}>
                  {STATUS[item.status] ?? item.status}
                </Text>
              </View>
              <Text className="sheet-list-label">{item.game_label}</Text>
              {item.recognition_status && (
                <Text className="sheet-list-recognition">
                  {item.recognition_status === "SUCCEEDED"
                    ? "识别完成"
                    : item.recognition_status === "FAILED"
                      ? "识别未完成，可手工录入"
                      : item.next_attempt_at
                        ? `等待自动继续 · ${countdown(item.next_attempt_at)}`
                        : "正在识别"}
                </Text>
              )}
            </View>
            {item.scoresheet_id ? (
              <Button
                className="sheet-list-action"
                onClick={() => Taro.navigateTo({ url: `/scoresheet/pages/editor/index?id=${item.scoresheet_id}` })}
              >
                {item.publication_number
                  ? adminRole === "SUPERADMIN" ? "查看 / 纠错" : "查看记录表"
                  : "打开核对"}
              </Button>
            ) : (
              <Button
                className="sheet-list-action"
                disabled={uploading === item.game_id}
                onClick={() => void upload(item)}
              >
                {uploading === item.game_id ? "上传中…" : "上传原图"}
              </Button>
            )}
          </View>
        ))}
      </View>
      {!loading && !items.length && !message && <View className="sheet-list-state">没有符合条件的比赛。</View>}
      {total > 0 && (
        <View className="sheet-list-pagination">
          <Button disabled={page <= 1 || loading} onClick={() => setPage((value) => value - 1)}>
            上一页
          </Button>
          <Text>{page} / {Math.max(1, Math.ceil(total / pageSize))}</Text>
          <Button
            disabled={page * pageSize >= total || loading}
            onClick={() => setPage((value) => value + 1)}
          >
            下一页
          </Button>
        </View>
      )}
    </View>
  );
}

function countdown(value: string) {
  const seconds = Math.max(0, Math.ceil((new Date(value).getTime() - Date.now()) / 1000));
  if (seconds >= 60) return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")} 后重试`;
  return `${seconds} 秒后重试`;
}

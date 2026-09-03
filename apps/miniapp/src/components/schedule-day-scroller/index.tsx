import { Button, ScrollView, Text, View } from "@tarojs/components";
import { useEffect, useRef, useState } from "react";
import type { Game, ScheduleDay, ScheduleDays } from "@pkuba/api-client";

import { api } from "../../api";
import { formatDate } from "../../format";
import { GameTimeline } from "../game-timeline";
import {
  mergeScheduleDays,
  replaceScheduleRange,
  scheduleDayAnchor,
} from "./model";
import "./index.css";

export function ScheduleDayScroller({
  divisionId = "",
  refreshKey,
  mode = "public",
  onGameClick,
}: {
  divisionId?: string;
  refreshKey: number;
  mode?: "public" | "admin";
  onGameClick: (game: Game) => void;
}) {
  const [days, setDays] = useState<ScheduleDay[]>([]);
  const [focusDate, setFocusDate] = useState("");
  const [hasPrevious, setHasPrevious] = useState(false);
  const [hasNext, setHasNext] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [beforeLoading, setBeforeLoading] = useState(false);
  const [afterLoading, setAfterLoading] = useState(false);
  const [initialError, setInitialError] = useState("");
  const [beforeError, setBeforeError] = useState("");
  const [afterError, setAfterError] = useState("");
  const [scrollTarget, setScrollTarget] = useState("");
  const daysRef = useRef<ScheduleDay[]>([]);
  const divisionRef = useRef(divisionId);
  const initialBusyRef = useRef<number | null>(null);
  const refreshBusyRef = useRef<number | null>(null);
  const beforeBusyRef = useRef<number | null>(null);
  const afterBusyRef = useRef<number | null>(null);
  const requestVersionRef = useRef(0);

  function commitDays(next: ScheduleDay[]) {
    daysRef.current = next;
    setDays(next);
  }

  function applyMetadata(result: ScheduleDays) {
    setFocusDate(result.focus_date ?? "");
    setHasPrevious(result.has_previous);
    setHasNext(result.has_next);
  }

  function queryFor(direction: "initial" | "before" | "after" | "range", params = "") {
    const query = new URLSearchParams({ direction, day_count: "5" });
    if (divisionId) query.set("division_id", divisionId);
    if (params) {
      const supplied = new URLSearchParams(params);
      supplied.forEach((value, key) => query.set(key, value));
    }
    return `?${query.toString()}`;
  }

  async function loadInitial(force = false) {
    if (initialBusyRef.current !== null && !force) return;
    const requestVersion = ++requestVersionRef.current;
    initialBusyRef.current = requestVersion;
    setInitialLoading(true);
    setInitialError("");
    try {
      const result = await api.getScheduleDays(queryFor("initial"));
      if (requestVersion !== requestVersionRef.current) return;
      const anchor = result.focus_date ? scheduleDayAnchor(result.focus_date) : "";
      setScrollTarget(anchor);
      commitDays(result.days);
      applyMetadata(result);
    } catch (reason: unknown) {
      if (requestVersion === requestVersionRef.current) setInitialError(messageOf(reason));
    } finally {
      if (requestVersion === requestVersionRef.current) setInitialLoading(false);
      if (initialBusyRef.current === requestVersion) initialBusyRef.current = null;
    }
  }

  async function revalidateLoadedRange() {
    const requestVersion = requestVersionRef.current;
    if (refreshBusyRef.current === requestVersion) return;
    const current = daysRef.current;
    if (!current.length) {
      await loadInitial();
      return;
    }
    const dateFrom = current[0].date;
    const dateTo = current[current.length - 1].date;
    refreshBusyRef.current = requestVersion;
    setRefreshing(true);
    try {
      const result = await api.getScheduleDays(queryFor(
        "range",
        `date_from=${dateFrom}&date_to=${dateTo}`,
      ));
      if (requestVersion !== requestVersionRef.current) return;
      commitDays(replaceScheduleRange(daysRef.current, result.days, dateFrom, dateTo));
      applyMetadata(result);
    } catch {
      // Keep the already visible schedule. Directional loading remains usable.
    } finally {
      if (requestVersion === requestVersionRef.current) setRefreshing(false);
      if (refreshBusyRef.current === requestVersion) refreshBusyRef.current = null;
    }
  }

  async function loadBefore() {
    const requestVersion = requestVersionRef.current;
    if (
      beforeBusyRef.current === requestVersion
      || !hasPrevious
      || !daysRef.current.length
    ) return;
    beforeBusyRef.current = requestVersion;
    setBeforeLoading(true);
    setBeforeError("");
    const anchor = daysRef.current[0].date;
    try {
      const result = await api.getScheduleDays(queryFor("before", `cursor=${anchor}`));
      if (requestVersion !== requestVersionRef.current) return;
      setScrollTarget(scheduleDayAnchor(anchor));
      commitDays(mergeScheduleDays(daysRef.current, result.days));
      setHasPrevious(result.has_previous);
    } catch (reason: unknown) {
      if (requestVersion === requestVersionRef.current) setBeforeError(messageOf(reason));
    } finally {
      if (beforeBusyRef.current === requestVersion) beforeBusyRef.current = null;
      if (requestVersion === requestVersionRef.current) setBeforeLoading(false);
    }
  }

  async function loadAfter() {
    const requestVersion = requestVersionRef.current;
    if (
      afterBusyRef.current === requestVersion
      || !hasNext
      || !daysRef.current.length
    ) return;
    afterBusyRef.current = requestVersion;
    setAfterLoading(true);
    setAfterError("");
    const anchor = daysRef.current[daysRef.current.length - 1].date;
    try {
      const result = await api.getScheduleDays(queryFor("after", `cursor=${anchor}`));
      if (requestVersion !== requestVersionRef.current) return;
      commitDays(mergeScheduleDays(daysRef.current, result.days));
      setHasNext(result.has_next);
    } catch (reason: unknown) {
      if (requestVersion === requestVersionRef.current) setAfterError(messageOf(reason));
    } finally {
      if (afterBusyRef.current === requestVersion) afterBusyRef.current = null;
      if (requestVersion === requestVersionRef.current) setAfterLoading(false);
    }
  }

  useEffect(() => {
    const divisionChanged = divisionRef.current !== divisionId;
    if (divisionChanged) {
      divisionRef.current = divisionId;
      commitDays([]);
      setFocusDate("");
      setScrollTarget("");
      setInitialError("");
      setBeforeError("");
      setAfterError("");
      setBeforeLoading(false);
      setAfterLoading(false);
      setRefreshing(false);
      void loadInitial(true);
      return;
    }
    if (!daysRef.current.length) void loadInitial();
    else void revalidateLoadedRange();
    // refreshKey deliberately revalidates on every page show without expanding the window.
  }, [divisionId, refreshKey]);

  if (initialLoading && !days.length) {
    return <View className="schedule-window-skeleton" aria-label="正在读取赛程">
      {[0, 1, 2].map((value) => <View className="schedule-skeleton-row" key={value} />)}
    </View>;
  }
  if (initialError && !days.length) {
    return <View className="schedule-window-state">
      <Text>{initialError}</Text>
      <Button onClick={() => void loadInitial()}>重新加载</Button>
    </View>;
  }
  if (!days.length) {
    return <View className="schedule-window-state"><Text>当前赛季暂无赛程。</Text></View>;
  }

  return <View className={`schedule-window ${mode === "admin" ? "is-admin" : ""}`}>
    {refreshing && <View className="schedule-refreshing"><Text>正在更新赛程</Text></View>}
    <ScrollView
      className="schedule-day-scroll"
      scrollY
      enhanced
      showScrollbar={false}
      upperThreshold={100}
      lowerThreshold={160}
      scrollIntoView={scrollTarget}
      onScroll={() => {
        if (scrollTarget) setScrollTarget("");
      }}
      onScrollToUpper={() => void loadBefore()}
      onScrollToLower={() => void loadAfter()}
    >
      <View className="schedule-scroll-content">
        <DirectionalState
          loading={beforeLoading}
          error={beforeError}
          onRetry={() => void loadBefore()}
        />
        {days.map((day) => (
          <View
            className="schedule-day-section"
            id={scheduleDayAnchor(day.date)}
            key={day.date}
          >
            <View className="schedule-day-heading">
              <Text className="schedule-day-title">{formatDate(day.date)}</Text>
              <Text className="schedule-day-count">{day.games.length} 场</Text>
            </View>
            {day.games.length ? (
              <GameTimeline games={day.games} showDates={false} onGameClick={onGameClick} />
            ) : (
              <View className="schedule-today-empty"><Text>今日无比赛</Text></View>
            )}
          </View>
        ))}
        <DirectionalState
          loading={afterLoading}
          error={afterError}
          onRetry={() => void loadAfter()}
        />
      </View>
    </ScrollView>
    {focusDate && <Button
      className="return-today"
      onClick={() => setScrollTarget(scheduleDayAnchor(focusDate))}
    >回到今天</Button>}
  </View>;
}

function DirectionalState({
  loading,
  error,
  onRetry,
}: {
  loading: boolean;
  error: string;
  onRetry: () => void;
}) {
  if (loading) return <View className="direction-state pulse"><Text>正在加载</Text></View>;
  if (error) return <Button className="direction-retry" onClick={onRetry}>{error} · 重新加载</Button>;
  return null;
}

function messageOf(reason: unknown) {
  return reason instanceof Error && reason.message ? reason.message : "赛程读取失败";
}

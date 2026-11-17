import { Button, Picker, Text, View } from "@tarojs/components";
import { useDidHide, useDidShow } from "@tarojs/taro";
import { useEffect, useRef, useState } from "react";
import {
  ApiError, createIdempotencyKey, type PagedResponse, type RefereeAssignment,
  type RefereeAvailability, type RefereeContext, type RefereeCrew, type SetRefereeAvailability,
} from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import { navigateToOnce } from "../../navigation";
import { NotificationSubscribe } from "../../components/notification-subscribe";
import "./index.css";

type Pending = { token: string; seasonId: string; key: string; payload: SetRefereeAvailability };

export default function RefereePage() {
  const [context, setContext] = useState<RefereeContext | null>(null);
  const [availability, setAvailability] = useState<PagedResponse<RefereeAvailability>>();
  const [assignments, setAssignments] = useState<PagedResponse<RefereeAssignment>>();
  const [availabilityPage, setAvailabilityPage] = useState(1);
  const [assignmentPage, setAssignmentPage] = useState(1);
  const [day, setDay] = useState("");
  const [periodIndex, setPeriodIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [crews, setCrews] = useState<Record<string, RefereeCrew>>({});
  const [crewBusy, setCrewBusy] = useState("");
  const [loginRequired, setLoginRequired] = useState(false);
  const generation = useRef(0);
  const identity = useRef("");
  const running = useRef(false);
  const pendingRef = useRef<Pending | null>(null);

  const clearPrivateData = () => {
    setContext(null); setAvailability(undefined); setAssignments(undefined);
    setDay(""); setPeriodIndex(0); setNotice("");
    setCrews({}); setCrewBusy("");
  };
  const load = async (nextAvailabilityPage = availabilityPage, nextAssignmentPage = assignmentPage) => {
    const token = getMiniAppSession();
    const sequence = ++generation.current;
    setCrews({});
    const current = () => sequence === generation.current && token === getMiniAppSession();
    if (token !== identity.current) {
      clearPrivateData();
      pendingRef.current = null; setPending(null);
      nextAvailabilityPage = 1; nextAssignmentPage = 1;
      setAvailabilityPage(1); setAssignmentPage(1);
      identity.current = token;
    }
    setLoginRequired(!token);
    if (!token) { clearPrivateData(); setLoading(false); return; }
    setLoading(true); setError("");
    try {
      const nextContext = await api.getRefereeContext(token);
      const [nextAvailability, nextAssignments] = await Promise.all([
        api.listRefereeAvailability(nextContext.season_id, token, nextAvailabilityPage),
        api.listRefereeAssignments(nextContext.season_id, token, nextAssignmentPage),
      ]);
      if (!current()) return;
      setContext(nextContext); setAvailability(nextAvailability); setAssignments(nextAssignments);
      setAvailabilityPage(nextAvailabilityPage); setAssignmentPage(nextAssignmentPage);
    } catch (reason) {
      if (!current()) return;
      clearPrivateData();
      setError(reason instanceof Error ? reason.message : "执裁信息读取失败");
      if (reason instanceof ApiError && reason.status === 401) setLoginRequired(true);
    } finally { if (current()) setLoading(false); }
  };

  useDidShow(() => { void load(); });
  useDidHide(() => { generation.current += 1; clearPrivateData(); });
  useEffect(() => () => { generation.current += 1; }, []);

  const perform = async (command: Pending) => {
    if (running.current || command.token !== getMiniAppSession()) return;
    running.current = true;
    const sequence = generation.current;
    const current = () => sequence === generation.current && command.token === getMiniAppSession();
    pendingRef.current = command; setPending(command); setBusy(true); setError(""); setNotice("");
    try {
      await api.setRefereeAvailability(command.seasonId, command.payload, command.token, command.key);
      if (!current()) return;
      pendingRef.current = null; setPending(null);
      setNotice(command.payload.active ? "可用时段已报名。" : "已撤回该时段报名。");
      await load();
    } catch (reason) {
      if (!current()) return;
      setError(reason instanceof Error ? reason.message : "操作结果尚未确认，请核对上次操作。");
      if (reason instanceof ApiError && reason.status < 500) {
        pendingRef.current = null; setPending(null);
      }
    } finally { running.current = false; setBusy(false); }
  };

  const change = (payload: SetRefereeAvailability) => {
    const token = getMiniAppSession();
    if (!token || !context || pendingRef.current) return;
    void perform({ token, seasonId: context.season_id, key: createIdempotencyKey(), payload });
  };
  const period = context?.periods[periodIndex];
  const locked = loading || busy || Boolean(pending);
  const qualified = Boolean(context?.qualification?.active);

  const showCrew = async (gameId: string) => {
    const token = getMiniAppSession();
    if (!token || crewBusy) return;
    const sequence = generation.current;
    const current = () => sequence === generation.current && token === getMiniAppSession();
    setCrewBusy(gameId); setError("");
    try {
      const crew = await api.getRefereeCrew(gameId, token);
      if (current()) setCrews((previous) => ({ ...previous, [gameId]: crew }));
    } catch (reason) {
      if (current()) {
        setCrews({});
        setError(reason instanceof Error ? reason.message : "本场执裁组读取失败");
      }
    } finally { if (current()) setCrewBusy(""); }
  };

  return <View className="page referee-page">
    <Text className="page-title">裁判与记录台</Text>
    {loading && <Text className="referee-detail">正在读取报名和选派安排…</Text>}
    {loginRequired && <View className="referee-card">
      <Text>请先微信登录，再查看个人报名和执裁安排。</Text>
      <Button onClick={() => void navigateToOnce("/pages/auth/index")}>前往登录</Button>
    </View>}
    {error && <View className="referee-warning">{error}</View>}
    {notice && <View className="referee-notice">{notice}</View>}
    {pending && !busy && <View className="referee-card">
      <Text>上次操作结果尚未确认，请先核对。</Text>
      <Button onClick={() => void perform(pending)}>核对上次操作</Button>
    </View>}
    {!loading && !loginRequired && <Button disabled={busy || Boolean(pending)} onClick={() => void load()}>刷新信息</Button>}
    {context && <>
      <NotificationSubscribe />
      <Text className="referee-detail">{context.season_name}</Text>
      {!qualified && <View className="referee-card">
        <Text>本赛季人员资格尚未开通或已停用，请联系管理员。</Text>
        <Text className="referee-detail">管理员使用你在小程序设置的唯一昵称开通资格。</Text>
      </View>}
      {qualified && <View className="referee-card">
        <Text className="referee-title">填写可用时段</Text>
        <Text className="referee-detail">选择有空的日期和固定时段。管理员按具体比赛选派，选派后直接生效。</Text>
        <Picker mode="date" start={context.starts_on} end={context.ends_on} value={day || context.starts_on}
          disabled={locked} onChange={(event) => setDay(event.detail.value)}>
          <View className="referee-field">日期：{day || "请选择日期"}</View>
        </Picker>
        <Picker mode="selector" range={context.periods.map((item) => `${item.name} ${item.start_time.slice(0, 5)}`)}
          value={periodIndex} disabled={locked || !context.periods.length}
          onChange={(event) => setPeriodIndex(Number(event.detail.value))}>
          <View className="referee-field">时段：{period ? `${period.name} ${period.start_time.slice(0, 5)}` : "暂无固定时段"}</View>
        </Picker>
        <Button className="referee-primary" disabled={locked || !day || !period} onClick={() => {
          if (!period) return;
          const existing = availability?.items.find((item) => item.date === day && item.period_id === period.id);
          change({ date: day, period_id: period.id, active: true, expected_version: existing?.version ?? 0 });
        }}>报名此时段</Button>
      </View>}
      <View className="referee-card">
        <Text className="referee-title">我的时段报名</Text>
        {!availability?.items.length && <Text className="referee-detail">暂无报名记录。</Text>}
        {availability?.items.map((item) => <View key={item.id} className="referee-row">
          <View><Text>{item.date} · {item.period_name}</Text>
            <Text className="referee-detail">{item.start_time.slice(0, 5)} · {item.active ? "已报名" : "已撤回"}</Text></View>
          {qualified && <Button size="mini" disabled={locked} onClick={() => change({
            date: item.date, period_id: item.period_id, active: !item.active, expected_version: item.version,
          })}>{item.active ? "撤回报名" : "重新报名"}</Button>}
        </View>)}
        <Pager page={availabilityPage} total={availability?.total ?? 0} disabled={locked}
          onChange={(page) => void load(page, assignmentPage)} />
      </View>
      <View className="referee-card">
        <Text className="referee-title">我的执裁安排</Text>
        <Text className="referee-detail">管理员选派后直接生效，请按安排到场。</Text>
        {!assignments?.items.length && <Text className="referee-detail">暂无选派安排。</Text>}
        {assignments?.items.map((item) => <View className="referee-assignment" key={item.id}>
          <Text className="referee-role-label">{item.role_name}</Text>
          {!item.active && <Text className="referee-detail">原选派已撤销，请以新的选派为准。</Text>}
          <Text>{item.home} — {item.away}</Text>
          <Text>{item.date} · {item.period_name}</Text>
          <Text className="referee-detail">{item.venue_name} · {item.game_code}</Text>
          {item.schedule_changed && <View className="referee-warning">
            比赛安排已变化，原选派已作废。当前比赛：{item.current_date} · {item.current_period_name}。
          </View>}
          {item.active && !item.schedule_changed && <Button size="mini" disabled={Boolean(crewBusy)}
            onClick={() => void showCrew(item.game_id)}>
            {crewBusy === item.game_id ? "正在读取…" : "查看本场执裁组"}
          </Button>}
          {item.active && crews[item.game_id] && <View className="referee-crew">
            {crews[item.game_id].members.map((member) => <Text className="referee-detail" key={member.role}>
              {member.role_name}：{member.username}
            </Text>)}
          </View>}
          {item.game_status === "COMPLETED" && <Text className="referee-detail">比赛已完成</Text>}
          {item.game_status === "FORFEIT" && <Text className="referee-detail">比赛已判弃权</Text>}
        </View>)}
        <Pager page={assignmentPage} total={assignments?.total ?? 0} disabled={locked}
          onChange={(page) => void load(availabilityPage, page)} />
      </View>
    </>}
  </View>;
}

function Pager({ page, total, disabled, onChange }: {
  page: number; total: number; disabled: boolean; onChange: (page: number) => void;
}) {
  return <View className="referee-pager">
    <Button size="mini" disabled={disabled || page <= 1} onClick={() => onChange(page - 1)}>上一页</Button>
    <Text className="referee-detail">第 {page} 页 · {total} 项</Text>
    <Button size="mini" disabled={disabled || page * 10 >= total} onClick={() => onChange(page + 1)}>下一页</Button>
  </View>;
}

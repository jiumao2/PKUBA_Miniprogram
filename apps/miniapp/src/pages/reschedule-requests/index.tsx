import { Button, Checkbox, CheckboxGroup, Label, Text, View } from "@tarojs/components";
import Taro, { useDidShow, useRouter } from "@tarojs/taro";
import { useEffect, useState } from "react";
import type { RescheduleRequest, RescheduleVoterTeam } from "@pkuba/api-client";

import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import { formatDate } from "../../format";
import "../../role-workspace.css";
import "./index.css";

export default function RescheduleRequestsPage() {
  const router = useRouter();
  const focusedRequestId = router.params.request_id ?? "";
  const [items, setItems] = useState<RescheduleRequest[]>([]);
  const [view, setView] = useState<"active" | "history">("active");
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [voteRequestId, setVoteRequestId] = useState("");
  const [candidates, setCandidates] = useState<RescheduleVoterTeam[]>([]);
  const [selectedVoters, setSelectedVoters] = useState<string[]>([]);

  const load = async () => {
    const token = getMiniAppSession();
    if (!token) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      setItems(await api.listRescheduleRequests(token));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "申请读取失败");
    } finally {
      setLoading(false);
    }
  };

  useDidShow(() => { void load(); });
  useEffect(() => {
    if (!focusedRequestId || !items.length) return;
    const focused = items.find((item) => item.id === focusedRequestId);
    if (!focused) return;
    setView(focused.is_terminal ? "history" : "active");
    setTimeout(() => {
      void Taro.pageScrollTo({ selector: `#request-${focusedRequestId}`, duration: 260 });
    }, 80);
  }, [focusedRequestId, items]);
  const shown = items
    .filter((item) => view === "active" ? !item.is_terminal : item.is_terminal)
    .sort((left, right) => Number(right.id === focusedRequestId) - Number(left.id === focusedRequestId));

  const confirmAction = async (
    item: RescheduleRequest,
    title: string,
    content: string,
    action: (token: string) => Promise<unknown>,
  ) => {
    const token = getMiniAppSession();
    if (!token) return;
    const result = await Taro.showModal({
      title,
      content,
      confirmText: "继续",
      confirmColor: "#c91f26",
    });
    if (!result.confirm) return;
    setBusyId(item.id);
    setError("");
    try {
      await action(token);
      await load();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    } finally {
      setBusyId("");
    }
  };

  const openVote = async (item: RescheduleRequest) => {
    const token = getMiniAppSession();
    if (!token) return;
    setBusyId(item.id);
    setError("");
    try {
      setCandidates(await api.getRescheduleVoterCandidates(item.id, token));
      setSelectedVoters([]);
      setVoteRequestId(item.id);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "投票球队读取失败");
    } finally {
      setBusyId("");
    }
  };

  const submitVote = async (item: RescheduleRequest) => {
    const token = getMiniAppSession();
    if (!token || !selectedVoters.length) return;
    setBusyId(item.id);
    setError("");
    try {
      await api.decideRescheduleAsAdmin(item.id, {
        expected_version: item.version,
        action: "vote",
        selected_team_ids: selectedVoters,
      }, token);
      setVoteRequestId("");
      await load();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "发起投票失败");
    } finally {
      setBusyId("");
    }
  };

  return (
    <View className="page request-list-page">
      <Text className="page-title">调赛申请</Text>
      <View className="subpage-tabs">
        <View className={`subpage-tab ${view === "active" ? "is-active" : ""}`} onClick={() => setView("active")}>
          <Text>进行中</Text>
        </View>
        <View className={`subpage-tab ${view === "history" ? "is-active" : ""}`} onClick={() => setView("history")}>
          <Text>历史记录</Text>
        </View>
      </View>

      {loading && <View className="state"><Text className="state-detail">正在读取申请状态…</Text></View>}
      {!loading && !shown.length && (
        <View className="state"><Text className="state-detail">这里暂时没有申请。</Text></View>
      )}
      <View className="request-list">
        {shown.map((item) => (
          <RequestCard
            item={item}
            focused={item.id === focusedRequestId}
            busy={busyId === item.id}
            voting={voteRequestId === item.id}
            candidates={candidates}
            selectedVoters={selectedVoters}
            setSelectedVoters={setSelectedVoters}
            onOpenVote={() => void openVote(item)}
            onSubmitVote={() => void submitVote(item)}
            onAction={(kind) => {
              if (kind === "WITHDRAW") void confirmAction(item, "撤回申请", "撤回后会释放原比赛锁和目标场地预留。", (token) => api.withdrawReschedule(item.id, item.version, token));
              if (kind === "OPPONENT_ACCEPT") void confirmAction(item, "同意调赛", "同周申请会立即生效；跨周申请会进入管理员处理。", (token) => api.respondToRescheduleOpponent(item.id, { expected_version: item.version, accept: true }, token));
              if (kind === "OPPONENT_REJECT") void confirmAction(item, "拒绝调赛", "拒绝后申请结束并释放预留。", (token) => api.respondToRescheduleOpponent(item.id, { expected_version: item.version, accept: false }, token));
              if (kind === "VOTER_ACCEPT") void confirmAction(item, "同意申请", "您的球队将记录为同意。", (token) => api.respondAsSelectedTeam(item.id, { expected_version: item.version, accept: true }, token));
              if (kind === "VOTER_REJECT") void confirmAction(item, "拒绝申请", "拒绝后申请结束并释放预留。", (token) => api.respondAsSelectedTeam(item.id, { expected_version: item.version, accept: false }, token));
              if (kind === "ADMIN_APPROVE") void confirmAction(item, "批准跨周调赛", "比赛将立即移动到已预留的目标时段。", (token) => api.decideRescheduleAsAdmin(item.id, { expected_version: item.version, action: "approve", selected_team_ids: [] }, token));
              if (kind === "ADMIN_REJECT") void confirmAction(item, "拒绝跨周调赛", "申请结束并释放目标预留。", (token) => api.decideRescheduleAsAdmin(item.id, { expected_version: item.version, action: "reject", selected_team_ids: [] }, token));
              if (kind === "FINAL_APPROVE") void confirmAction(item, "终审通过", "比赛将移动到已预留的目标时段。", (token) => api.decideRescheduleFinal(item.id, { expected_version: item.version, accept: true }, token));
              if (kind === "FINAL_REJECT") void confirmAction(item, "终审拒绝", "申请结束并释放目标预留。", (token) => api.decideRescheduleFinal(item.id, { expected_version: item.version, accept: false }, token));
              if (kind === "ADMIN_CANCEL") void confirmAction(item, "管理员取消", "该操作会结束申请并释放锁和预留。", (token) => api.cancelRescheduleAsAdmin(item.id, item.version, token));
            }}
            key={item.id}
          />
        ))}
      </View>
      {error && <View className="flow-feedback">{error}</View>}
    </View>
  );
}

type ActionKind =
  | "WITHDRAW" | "OPPONENT_ACCEPT" | "OPPONENT_REJECT"
  | "VOTER_ACCEPT" | "VOTER_REJECT" | "ADMIN_APPROVE" | "ADMIN_REJECT"
  | "FINAL_APPROVE" | "FINAL_REJECT" | "ADMIN_CANCEL";

function RequestCard({
  item, focused, busy, voting, candidates, selectedVoters, setSelectedVoters,
  onOpenVote, onSubmitVote, onAction,
}: {
  item: RescheduleRequest;
  focused: boolean;
  busy: boolean;
  voting: boolean;
  candidates: RescheduleVoterTeam[];
  selectedVoters: string[];
  setSelectedVoters: (value: string[]) => void;
  onOpenVote: () => void;
  onSubmitVote: () => void;
  onAction: (kind: ActionKind) => void;
}) {
  const has = (action: string) => item.actions.includes(action);
  return (
    <View
      id={`request-${item.id}`}
      className={`request-card ${item.game.division_gender === "WOMEN" ? "is-women" : ""} ${focused ? "is-focused" : ""}`}
    >
      <View className="request-heading">
        <View>
          <Text className="request-division">{item.game.division_name} · {item.request_type_label}</Text>
          <Text className="request-teams">{item.original_home_name} vs {item.original_away_name}</Text>
        </View>
        <Text className={`request-status status-${item.status.toLowerCase()}`}>{item.status_label}</Text>
      </View>
      <View className="request-route">
        <View className="request-slot">
          <Text className="request-slot-label">原赛程</Text>
          <Text className="request-slot-main">{formatDate(item.original_date)} · {item.original_start_time}</Text>
          <Text className="request-slot-meta">{item.original_venue_name}</Text>
        </View>
        <Text className="request-arrow">→</Text>
        <View className="request-slot target-slot">
          <Text className="request-slot-label">目标赛程</Text>
          <Text className="request-slot-main">{formatDate(item.target_date)} · {item.target_start_time}</Text>
          <Text className="request-slot-meta">{item.target_venue_name}</Text>
        </View>
      </View>
      <Text className="request-requester">申请方 · {item.requester_team_name}</Text>
      {!!item.confirmations.length && (
        <View className="confirmation-list">
          {item.confirmations.map((confirmation) => (
            <Text className="confirmation-item" key={confirmation.id}>
              {confirmation.team_name} · {responseLabel(confirmation.response)}
            </Text>
          ))}
        </View>
      )}

      {!!item.actions.length && (
        <View className="request-actions">
          {has("RESPOND_OPPONENT") && <><Button disabled={busy} onClick={() => onAction("OPPONENT_ACCEPT")}>同意</Button><Button className="outline" disabled={busy} onClick={() => onAction("OPPONENT_REJECT")}>拒绝</Button></>}
          {has("RESPOND_SELECTED_TEAM") && <><Button disabled={busy} onClick={() => onAction("VOTER_ACCEPT")}>同意</Button><Button className="outline" disabled={busy} onClick={() => onAction("VOTER_REJECT")}>拒绝</Button></>}
          {has("ADMIN_APPROVE") && <Button disabled={busy} onClick={() => onAction("ADMIN_APPROVE")}>直接批准</Button>}
          {has("ADMIN_REJECT") && <Button className="outline" disabled={busy} onClick={() => onAction("ADMIN_REJECT")}>拒绝</Button>}
          {has("ADMIN_START_VOTE") && <Button className="outline" disabled={busy} onClick={onOpenVote}>指定球队投票</Button>}
          {has("ADMIN_FINAL_APPROVE") && <Button disabled={busy} onClick={() => onAction("FINAL_APPROVE")}>终审通过</Button>}
          {has("ADMIN_FINAL_REJECT") && <Button className="outline" disabled={busy} onClick={() => onAction("FINAL_REJECT")}>终审拒绝</Button>}
          {has("WITHDRAW") && <Button className="outline" disabled={busy} onClick={() => onAction("WITHDRAW")}>撤回申请</Button>}
          {has("ADMIN_CANCEL") && <Button className="danger" disabled={busy} onClick={() => onAction("ADMIN_CANCEL")}>管理员取消</Button>}
        </View>
      )}

      {voting && (
        <View className="voter-panel">
          <Text className="voter-title">指定确认球队</Text>
          <CheckboxGroup onChange={(event) => setSelectedVoters(event.detail.value)}>
            {candidates.map((team) => (
              <Label className="voter-option" key={team.id}>
                <Checkbox value={team.id} checked={selectedVoters.includes(team.id)} color="#c91f26" />
                <Text>{team.name}{team.group_name ? ` · ${team.group_name}` : ""}</Text>
              </Label>
            ))}
          </CheckboxGroup>
          {!candidates.length && <Text className="voter-empty">没有可指定的同组球队。</Text>}
          <Button className="flow-primary" disabled={busy || !selectedVoters.length} onClick={onSubmitVote}>发起投票</Button>
        </View>
      )}
    </View>
  );
}

function responseLabel(response: string) {
  if (response === "ACCEPTED") return "已同意";
  if (response === "REJECTED") return "已拒绝";
  return "待确认";
}

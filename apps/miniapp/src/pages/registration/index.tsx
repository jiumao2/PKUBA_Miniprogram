import { Button, Input, Picker, Text, View } from "@tarojs/components";
import Taro, { useDidHide, useDidShow, useRouter } from "@tarojs/taro";
import { useEffect, useRef, useState } from "react";
import {
  ApiError, createIdempotencyKey, registrationStatusLabels, type Registration,
  type RegistrationContext, type RegistrationForm, type RegistrationHistory, type PagedResponse,
} from "@pkuba/api-client";
import { api } from "../../api";
import { getMiniAppSession } from "../../auth";
import { navigateToOnce } from "../../navigation";
import { NotificationSubscribe } from "../../components/notification-subscribe";
import "./index.css";

type Command = { token: string; run: () => Promise<unknown>; label: string };
const emptyForm = (): RegistrationForm => ({ team_name: "", uniform_color: "", leader_name: "", phone: "", email: "", players: [] });

export default function RegistrationPage() {
  const route = useRouter();
  const [context, setContext] = useState<RegistrationContext>();
  const [registration, setRegistration] = useState<Registration>();
  const [form, setForm] = useState<RegistrationForm>(emptyForm);
  const [history, setHistory] = useState<PagedResponse<RegistrationHistory>>();
  const [teamName, setTeamName] = useState("");
  const [reason, setReason] = useState("");
  const [username, setUsername] = useState("");
  const [contact, setContact] = useState({ leader_name: "", phone: "", email: "" });
  const [editingChange, setEditingChange] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loginRequired, setLoginRequired] = useState(false);
  const [pending, setPending] = useState<Command>();
  const generation = useRef(0);
  const identity = useRef("");
  const running = useRef(false);
  const unresolved = useRef<Command>();
  const activeSeason = useRef(route.params.season_id ?? "");
  const clear = () => {
    setContext(undefined); setRegistration(undefined); setForm(emptyForm()); setHistory(undefined);
    setTeamName(""); setReason(""); setUsername(""); setContact({ leader_name: "", phone: "", email: "" }); setEditingChange(false);
  };
  const load = async () => {
    const token = getMiniAppSession(), seq = ++generation.current;
    const current = () => seq === generation.current && token === getMiniAppSession();
    clear(); setError(""); setLoading(true); setLoginRequired(!token);
    if (identity.current !== token) { unresolved.current = undefined; setPending(undefined); identity.current = token; }
    if (!token) { setLoading(false); return; }
    try {
      const next = await api.getRegistrationContext(token, activeSeason.current);
      let item: Registration | undefined;
      let log: PagedResponse<RegistrationHistory> | undefined;
      if (next.registration) [item, log] = await Promise.all([
        api.getRegistration(next.registration.id, token), api.getRegistrationHistory(next.registration.id, token),
      ]);
      if (!current()) return;
      activeSeason.current = next.season_id;
      setContext(next); setRegistration(item); setForm(item?.form ?? emptyForm()); setHistory(log);
    } catch (e) {
      if (!current()) return;
      clear(); setError(e instanceof Error ? e.message : "报名信息读取失败");
      if (e instanceof ApiError && e.status === 401) setLoginRequired(true);
    } finally { if (current()) setLoading(false); }
  };
  useDidShow(() => { void load(); });
  useDidHide(() => { generation.current += 1; clear(); setNotice(""); setError(""); });
  useEffect(() => () => { generation.current += 1; }, []);
  const perform = async (command: Command) => {
    if (running.current || command.token !== getMiniAppSession()) return;
    running.current = true; unresolved.current = command; setPending(command); setBusy(true); setError(""); setNotice("");
    const seq = generation.current;
    const current = () => seq === generation.current && command.token === getMiniAppSession();
    try {
      await command.run();
      if (!current()) return;
      unresolved.current = undefined; setPending(undefined); setNotice(`${command.label}已保存。`); await load();
    } catch (e) {
      if (!current()) return;
      setError(e instanceof Error ? e.message : "操作结果尚未确认");
      if (e instanceof ApiError && e.status < 500) { unresolved.current = undefined; setPending(undefined); }
    } finally { running.current = false; setBusy(false); }
  };
  const command = (label: string, run: (token: string, key: string) => Promise<unknown>) => {
    const token = getMiniAppSession(), key = createIdempotencyKey();
    if (!token || unresolved.current) return;
    void perform({ token, label, run: () => run(token, key) });
  };
  const save = async (submit: boolean) => {
    if (!registration) return;
    const item = registration, payload = { expected_version: item.version, form: { ...form, players: form.players?.map((p) => ({ ...p })) }, submit, reason };
    if (submit) {
      const answer = await Taro.showModal({ title: item.team_id ? "提交名单变更" : "提交报名", content: "提交后由管理员审核，审核期间不能覆盖本版本。确认提交？" });
      if (!answer.confirm) return;
    }
    command(submit ? "申请" : "草稿", (token, key) => api.saveRegistration(item.id, payload, token, key));
  };
  const changeField = (key: keyof RegistrationForm, value: string) => setForm((old) => ({ ...old, [key]: value }));
  const updatePlayer = (index: number, key: string, value: string) => setForm((old) => ({ ...old,
    players: (old.players ?? []).map((p, i) => i === index ? { ...p, [key]: value } : p),
  }));
  const historyPage = async (page: number) => {
    const token = getMiniAppSession(), seq = generation.current;
    if (!registration || !token) return;
    setBusy(true); setError("");
    try {
      const result = await api.getRegistrationHistory(registration.id, token, page);
      if (seq === generation.current && token === getMiniAppSession()) setHistory(result);
    } catch (e) { if (seq === generation.current) setError(e instanceof Error ? e.message : "记录读取失败"); }
    finally { if (seq === generation.current) setBusy(false); }
  };
  const locked = loading || busy || Boolean(pending);
  const pendingReview = ["PENDING", "CHANGE_PENDING"].includes(registration?.status ?? "");
  const editable = Boolean(context?.policy.open && !pendingReview && (!registration?.team_id || editingChange));

  return <View className="page registration-page"><Text className="page-title">球队报名</Text>
    {loading && <Text>正在读取报名信息…</Text>}
    {loginRequired && <Button onClick={() => void navigateToOnce("/pages/auth/index")}>微信登录后报名</Button>}
    {error && <View className="registration-error">{error}</View>}{notice && <Text className="registration-notice">{notice}</Text>}
    {pending && !busy && <Button onClick={() => void perform(pending)}>核对上次操作</Button>}
    {!loading && !loginRequired && <Button disabled={busy || Boolean(pending)} onClick={() => void load()}>刷新报名</Button>}
    {context && <><Picker mode="selector" range={context.seasons.map((s) => s.name)} value={context.seasons.findIndex((s) => s.id === context.season_id)} disabled={locked} onChange={async (e) => {
      const next = context.seasons[Number(e.detail.value)];
      if (!next || next.id === context.season_id) return;
      const result = await Taro.showModal({ title: "切换报名赛季", content: "未保存的填写内容将丢弃，确认切换？" });
      if (result.confirm) { activeSeason.current = next.id; await load(); }
    }}><Text>报名赛季：{context.season_name} ▾</Text></Picker><View className="registration-card">
      <Text>截止时间：{context.policy.deadline ? new Date(context.policy.deadline).toLocaleString() : "尚未设置"}</Text>
      <Text>{context.policy.open ? "当前允许报名和发起名单变更" : "当前报名未开放或已截止"}</Text>
      <Text>每赛季一人一队；同一学生按学号只能报一队。资料仅本队当前领队和管理员可见。</Text>
    </View>
      {!registration && !context.existing_team && <View className="registration-card"><Text>创建球队后即成为该队报名领队</Text>
        <Field label="球队名称" value={teamName} disabled={locked || !context.policy.open} onChange={setTeamName} maxLength={120} />
        <Button disabled={locked || !context.policy.open || !teamName.trim()} onClick={() => {
          const name = teamName.trim(), seasonId = context.season_id;
          command("球队", (token, key) => api.createRegistration(seasonId, name, token, key));
        }}>创建球队</Button></View>}
      {!registration && context.existing_team && <View className="registration-card"><Text>你已绑定本赛季球队，可补充报名资料；正式名单修改仍须审核。</Text>
        <Button disabled={locked || !context.policy.open} onClick={() => command("报名资料关联", (token, key) => api.createRegistration(context.season_id, "本队", token, key))}>关联现有球队报名资料</Button>
        <Button onClick={() => void navigateToOnce("/pages/leader/index")}>领队工作台</Button></View>}
      {registration && <>
        <View className="registration-card"><Text className="registration-heading">{registration.team_name} · {registrationStatusLabels[registration.status]}</Text>
          {registration.team_id && !editingChange && <Text>正式名单已锁定，增删队员须在截止前提交变更申请。</Text>}
          {registration.team_id && !editingChange && <Button disabled={locked || !context.policy.open || pendingReview} onClick={() => setEditingChange(true)}>申请增删队员</Button>}
          {([ ["team_name", "球队名称", 120], ["uniform_color", "球服颜色", 80], ["leader_name", "领队姓名", 80], ["phone", "领队电话", 40], ["email", "领队邮箱", 254] ] as const).map(([key, label, length]) =>
            <Field key={key} label={label} value={form[key] ?? ""} maxLength={length} disabled={locked || !editable || (key === "team_name" && Boolean(registration.team_id))} onChange={(v) => changeField(key, v)} />)}
        </View>
        <View className="registration-card"><Text className="registration-heading">队员名单（{form.players?.length ?? 0} 人）</Text>
          {(form.players ?? []).map((p, i) => <View className="registration-player" key={p.id || `new-${i}`}><Text>队员 {i + 1}</Text>
            {([ ["college", "学院", 120], ["name", "姓名", 80], ["student_number", "学号", 64], ["jersey_number", "球衣号码", 2] ] as const).map(([key, label, length]) =>
              <Field key={key} label={label} value={p[key] ?? ""} maxLength={length} disabled={locked || !editable || (key === "student_number" && Boolean(registration.approved_form?.players?.find((old) => old.id === p.id)?.student_number))} onChange={(v) => updatePlayer(i, key, v)} />)}
            {editable && <Button disabled={locked} onClick={() => setForm((old) => ({ ...old, players: (old.players ?? []).filter((_, n) => n !== i) }))}>移除本行</Button>}
          </View>)}
          {editable && <Button disabled={locked} onClick={() => setForm((old) => ({ ...old, players: [...(old.players ?? []), { id: "", college: "", name: "", student_number: "", jersey_number: "" }] }))}>添加队员</Button>}
          {registration.team_id && editingChange && <Field label="名单变更原因" value={reason} disabled={locked} maxLength={500} onChange={setReason} />}
          {editable && <><Text>离开页面前请保存草稿；已通过的名单修改须提交审核。</Text>
            {!registration.team_id && <Button disabled={locked} onClick={() => void save(false)}>保存草稿</Button>}
            <Button disabled={locked || (Boolean(registration.team_id) && !reason.trim())} onClick={() => void save(true)}>{registration.team_id ? "提交名单变更" : "提交报名审核"}</Button></>}
        </View>
        <View className="registration-card"><Text className="registration-heading">领队传承</Text><Text>原领队发起，接任者接受后直接生效。你的访问权限随交接转移。</Text>
          <Field label="接任者唯一昵称" value={username} disabled={locked} maxLength={32} onChange={setUsername} />
          <Button disabled={locked || !username.trim()} onClick={async () => {
            const item = registration, name = username.trim();
            const result = await Taro.showModal({ title: "发起领队传承", content: `确认交接给“${name}”？对方接受后你将失去本队资料访问权。` });
            if (result.confirm) command("领队传承", (token, key) => api.initiateLeaderHandover(item.id, item.version, name, token, key));
          }}>发起传承</Button></View>
        {history && <View className="registration-card"><Text className="registration-heading">提交与审核记录</Text>{history.items.map((h) => <View className="registration-history" key={h.id}>
          <Text>版本 {h.version} · {h.decision === "APPROVED" ? "通过" : h.decision === "RETURNED" ? "已退回" : "待审核"}</Text>
          {h.reason && <Text>申请原因：{h.reason}</Text>}{h.review_reason && <Text>审核意见：{h.review_reason}</Text>}
        </View>)}<Button disabled={locked || history.page <= 1} onClick={() => void historyPage(history.page - 1)}>较新记录</Button>
          <Button disabled={locked || history.page * history.page_size >= history.total} onClick={() => void historyPage(history.page + 1)}>较早记录</Button></View>}
      </>}
      {context.handovers.map((h) => <View className="registration-card" key={h.id}><Text className="registration-heading">待处理交接：{h.team_name}</Text>
        <Text>{h.source_username} → {h.target_username}</Text><Text>接任者接受时填写自己的联系资料。</Text>
        {([ ["leader_name", "接任者姓名"], ["phone", "接任者电话"], ["email", "接任者邮箱"] ] as const).map(([key, label]) =>
          <Field key={key} label={label} value={contact[key]} disabled={locked} maxLength={key === "email" ? 254 : 80} onChange={(v) => setContact((old) => ({ ...old, [key]: v }))} />)}
        <Button disabled={locked} onClick={async () => {
          const values = { ...contact };
          const result = await Taro.showModal({ title: "接受领队传承", content: "接受后直接成为本队领队，无须管理员再次批准。确认接受？" });
          if (result.confirm) command("领队交接", (token, key) => api.resolveLeaderHandover(h.id, { expected_version: h.version, accept: true, ...values }, token, key));
        }}>接受传承（仅接任者）</Button>
        <Button disabled={locked} onClick={() => command("取消交接", (token, key) => api.resolveLeaderHandover(h.id, { expected_version: h.version, accept: false, leader_name: "", phone: "", email: "" }, token, key))}>拒绝或取消交接</Button>
      </View>)}
      <NotificationSubscribe />
    </>}
  </View>;
}

function Field({ label, value, disabled, onChange, maxLength }: { label: string; value: string; disabled: boolean; onChange: (v: string) => void; maxLength: number }) {
  return <View className="registration-field"><Text>{label}</Text><Input value={value} disabled={disabled} maxlength={maxLength} placeholder={`填写${label}`} onInput={(e) => onChange(e.detail.value)} /></View>;
}

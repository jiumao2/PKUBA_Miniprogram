import { useState } from "react";
import type {
  LifecycleCommand,
  LifecyclePreview,
  SeasonConfiguration,
} from "@pkuba/api-client";

type AdminClient = ReturnType<typeof import("@pkuba/api-client").createAdminClient>;

const statusLabels: Record<string, string> = {
  SETUP: "准备中",
  PRE_DRAW_PUBLIC: "占位赛程公开",
  ACTIVE: "正式业务开放",
  ARCHIVED: "已归档",
};

function previewSummary(preview: LifecyclePreview) {
  const lines = [
    `赛季：${statusLabels[preview.before_season_status] ?? preview.before_season_status} → ${statusLabels[preview.after_season_status] ?? preview.after_season_status}`,
    ...preview.impacts.map(
      (item) =>
        `${item.division_name}：${statusLabels[item.before_status] ?? item.before_status} → ${statusLabels[item.after_status] ?? item.after_status}`,
    ),
  ];
  if (preview.blockers.length) {
    lines.push("", ...preview.blockers.map((item) => `阻塞：${item.message}（${item.count}）`));
  }
  return lines.join("\n");
}

export function SeasonLifecyclePanel({
  client,
  configuration,
  dirty,
  onApplied,
}: {
  client: AdminClient;
  configuration: SeasonConfiguration;
  dirty: boolean;
  onApplied: () => Promise<void>;
}) {
  const [busyKey, setBusyKey] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (key: string, command: LifecycleCommand, verb: string) => {
    setBusyKey(key);
    setNotice(null);
    setError(null);
    try {
      const preview = await client.previewSeasonLifecycle(configuration.id, command);
      if (!preview.can_apply) {
        setError(previewSummary(preview));
        return;
      }
      if (!window.confirm(`${previewSummary(preview)}\n\n确认${verb}？该操作会写入审计日志。`)) {
        return;
      }
      await client.applySeasonLifecycle(configuration.id, {
        ...command,
        impact_hash: preview.impact_hash,
      });
      setNotice(`${verb}完成。`);
      await onApplied();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : `${verb}失败`);
    } finally {
      setBusyKey("");
    }
  };

  const disabled = dirty || Boolean(busyKey) || configuration.status === "ARCHIVED";

  return (
    <section className="season-lifecycle" aria-labelledby="season-lifecycle-title">
      <div className="season-section-heading">
        <div>
          <h2 id="season-lifecycle-title">赛季上线</h2>
          <p>先公开占位赛程，再按组别完成抽签并开放业务；归档后整季永久只读。</p>
        </div>
        <span>赛季 v{configuration.version}</span>
      </div>

      {dirty && (
        <p className="lifecycle-warning">请先保存或撤销上方配置修改，再执行状态迁移。</p>
      )}

      <div className="lifecycle-overview">
        <div className="lifecycle-season-state">
          <span>全局状态</span>
          <strong className={`lifecycle-status status-${configuration.status.toLowerCase()}`}>
            {statusLabels[configuration.status] ?? configuration.status}
          </strong>
          {configuration.status === "SETUP" && (
            <button
              className="primary-action"
              disabled={disabled}
              type="button"
              onClick={() =>
                void run(
                  "publish",
                  {
                    expected_season_version: configuration.version,
                    target_status: "PRE_DRAW_PUBLIC",
                  },
                  "公开占位赛程",
                )
              }
            >
              {busyKey === "publish" ? "正在检查…" : "公开占位赛程"}
            </button>
          )}
          {configuration.status !== "SETUP" && configuration.status !== "ARCHIVED" && (
            <button
              className="danger-action"
              disabled={disabled}
              type="button"
              onClick={() =>
                void run(
                  "archive",
                  {
                    expected_season_version: configuration.version,
                    target_status: "ARCHIVED",
                  },
                  "归档赛季",
                )
              }
            >
              {busyKey === "archive" ? "正在检查…" : "归档赛季"}
            </button>
          )}
        </div>

        <div className="lifecycle-division-list">
          {configuration.divisions.map((division) => {
            const key = `division-${division.id}`;
            const commandBase = {
              expected_season_version: configuration.version,
              division_id: division.id,
              expected_division_version: division.version,
            };
            return (
              <article key={division.id}>
                <div>
                  <span>{division.gender === "WOMEN" ? "女子" : "男子"}</span>
                  <strong>{division.name}</strong>
                  <small>v{division.version} · {division.game_count} 场</small>
                </div>
                <span className={`lifecycle-status status-${division.operation_status.toLowerCase()}`}>
                  {statusLabels[division.operation_status] ?? division.operation_status}
                </span>
                {division.operation_status === "PRE_DRAW_PUBLIC" && (
                  <button
                    className="secondary-action"
                    disabled={disabled}
                    type="button"
                    onClick={() =>
                      void run(
                        key,
                        { ...commandBase, target_status: "ACTIVE" },
                        `开放${division.name}`,
                      )
                    }
                  >
                    {busyKey === key ? "正在检查…" : "开放业务"}
                  </button>
                )}
                {division.operation_status === "ACTIVE" && (
                  <button
                    className="text-action"
                    disabled={disabled}
                    type="button"
                    onClick={() =>
                      void run(
                        key,
                        { ...commandBase, target_status: "SETUP" },
                        `撤回${division.name}`,
                      )
                    }
                  >
                    {busyKey === key ? "正在检查…" : "安全撤回"}
                  </button>
                )}
              </article>
            );
          })}
        </div>
      </div>
      {notice && <p className="lifecycle-notice success" role="status">{notice}</p>}
      {error && <pre className="lifecycle-notice error" role="alert">{error}</pre>}
    </section>
  );
}

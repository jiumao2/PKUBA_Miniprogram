import { useState } from "react";
import type {
  LifecycleCommand,
  LifecyclePreview,
  SeasonConfiguration,
} from "@pkuba/api-client";

type AdminClient = ReturnType<typeof import("@pkuba/api-client").createAdminClient>;

const statusLabels: Record<string, string> = {
  SETUP: "准备中",
  PUBLISHED: "已公开",
  ARCHIVED: "已归档",
};

function previewSummary(preview: LifecyclePreview) {
  const lines = [
    `赛季：${statusLabels[preview.before_season_status] ?? preview.before_season_status} → ${statusLabels[preview.after_season_status] ?? preview.after_season_status}`,
  ];
  if (preview.blockers.length) {
    lines.push(
      "",
      ...preview.blockers.map((item) => `阻塞：${item.message}（${item.count}）`),
    );
  }
  const references = Object.entries(preview.references).filter(([, count]) => count > 0);
  if (references.length) {
    lines.push("", `仍有关联业务数据：${references.map(([key, count]) => `${key} ${count}`).join("、")}`);
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
          <h2 id="season-lifecycle-title">赛季状态</h2>
          <p>赛季公开后，比赛能否操作由双方是否完成签位及业务数据实时判断。</p>
        </div>
      </div>

      {dirty && (
        <p className="lifecycle-warning">请先保存或撤销上方配置修改，再执行状态迁移。</p>
      )}

      <div className="lifecycle-overview">
        <div className="lifecycle-season-state">
          <span>当前状态</span>
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
                    target_status: "PUBLISHED",
                  },
                  "公开赛季",
                )
              }
            >
              {busyKey === "publish" ? "正在检查…" : "公开赛季"}
            </button>
          )}
          {configuration.status === "PUBLISHED" && (
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

        <div className="lifecycle-division-list" aria-label="组别数据概览">
          {configuration.divisions.map((division) => (
            <article key={division.id}>
              <div>
                <span>{division.gender === "WOMEN" ? "女子" : "男子"}</span>
                <strong>{division.name}</strong>
                <small>
                  {division.team_count} 队 · {division.group_count} 组 · {division.game_count} 场
                </small>
              </div>
              <span className="lifecycle-derived-state">按比赛数据自动开放</span>
            </article>
          ))}
        </div>
      </div>
      {notice && <p className="lifecycle-notice success" role="status">{notice}</p>}
      {error && <pre className="lifecycle-notice error" role="alert">{error}</pre>}
    </section>
  );
}

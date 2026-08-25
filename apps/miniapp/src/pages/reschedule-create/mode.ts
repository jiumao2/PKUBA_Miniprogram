export type RescheduleEntryMode = "same_week" | "cross_week";

export const RESCHEDULE_ENTRY_COPY = {
  same_week: {
    title: "普通调赛",
    shortDescription: "同一自然周内调整",
    processRoute: "ORDINARY",
    targetLabel: "普通流程",
    emptyMessage: "这场比赛当前没有满足容量、场地和球队冲突检查的同周目标时段。",
  },
  cross_week: {
    title: "跨周调赛",
    shortDescription: "可选择本周或跨周时段，是否跨轮次由管理员审核",
    processRoute: "HANDBOOK_REVIEW",
    targetLabel: "参赛手册审核",
    guidance:
      "可选择本周或跨周时段。请参阅《参赛手册》中的跨轮次调整规定和步骤；是否跨轮次由管理员审核，未跨轮次时管理员会直接按普通办法处理，无需重新提交。",
    emptyMessage: "这场比赛当前没有满足容量、场地和球队冲突检查的目标时段。",
  },
} as const;

export function parseRescheduleEntryMode(value: unknown): RescheduleEntryMode {
  return value === "cross_week" ? "cross_week" : "same_week";
}

export function targetsForEntryMode<T extends { request_type: string }>(
  targets: T[],
  mode: RescheduleEntryMode,
): T[] {
  if (mode === "cross_week") return targets;
  return targets.filter((target) => target.request_type === "SAME_WEEK");
}

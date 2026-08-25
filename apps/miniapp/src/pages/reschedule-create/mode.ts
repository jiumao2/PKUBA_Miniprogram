export type RescheduleEntryMode = "same_week" | "cross_week";

export const RESCHEDULE_ENTRY_COPY = {
  same_week: {
    title: "普通调赛",
    shortDescription: "同一自然周内调整",
    targetType: "SAME_WEEK",
    targetLabel: "普通调赛",
    emptyMessage: "这场比赛当前没有满足容量、场地和球队冲突检查的同周目标时段。",
  },
  cross_week: {
    title: "跨周调赛",
    shortDescription: "参阅《参赛手册》跨轮次调整；由管理员审核判定",
    targetType: "CROSS_WEEK",
    targetLabel: "跨周调赛",
    guidance:
      "参阅《参赛手册》中的跨轮次调整方式；是否跨轮次由管理员审核决定，未跨轮次请按普通调赛办法处理。",
    emptyMessage: "这场比赛当前没有满足容量、场地和球队冲突检查的跨周目标时段。",
  },
} as const;

export function parseRescheduleEntryMode(value: unknown): RescheduleEntryMode {
  return value === "cross_week" ? "cross_week" : "same_week";
}

export function targetsForEntryMode<T extends { request_type: string }>(
  targets: T[],
  mode: RescheduleEntryMode,
): T[] {
  return targets.filter((target) => target.request_type === RESCHEDULE_ENTRY_COPY[mode].targetType);
}

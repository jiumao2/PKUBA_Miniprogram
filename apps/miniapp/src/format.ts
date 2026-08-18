const weekdays = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

export function formatDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  const local = new Date(year, month - 1, day);
  return `${month}月${day}日 ${weekdays[local.getDay()]}`;
}

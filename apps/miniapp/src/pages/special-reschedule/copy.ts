export const STAFF_EMAIL = "pkubaoutward@163.com";

export async function copyStaffEmail(
  copy: (options: { data: string }) => Promise<unknown>,
  notify: (options: { title: string; icon: "success" | "none" }) => Promise<unknown>,
) {
  try {
    await copy({ data: STAFF_EMAIL });
    await notify({ title: "公邮已复制", icon: "success" });
    return true;
  } catch {
    await notify({ title: "复制失败，请长按邮箱复制", icon: "none" });
    return false;
  }
}

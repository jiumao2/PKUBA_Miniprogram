export function passwordCharacterCount(value: string) {
  return Array.from(value).length;
}

export function validateAdminRegistration(
  inviteCode: string,
  password: string,
  passwordConfirmation: string,
) {
  if (!inviteCode.trim()) return "请填写管理员邀请码。";
  if (passwordCharacterCount(password) < 4) return "网页密码至少需要 4 个字符。";
  if (password !== passwordConfirmation) return "两次输入的网页密码不一致。";
  return null;
}

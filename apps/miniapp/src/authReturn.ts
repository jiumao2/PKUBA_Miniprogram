const RETURN_ROUTES = {
  reschedule_ordinary: "/pages/reschedule-create/index?mode=same_week",
  reschedule_handbook: "/pages/reschedule-create/index?mode=cross_week",
  reschedule_requests: "/pages/reschedule-requests/index",
} as const;

export type AuthReturnEntry = keyof typeof RETURN_ROUTES;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Only these private entry points may resume after login; never accept a URL. */
export function authReturnUrl(params: Record<string, string | undefined>): string | null {
  const entry = params.return_to;
  if (!entry || !Object.prototype.hasOwnProperty.call(RETURN_ROUTES, entry)) return null;
  const url = RETURN_ROUTES[entry as AuthReturnEntry];
  return entry === "reschedule_requests" && UUID.test(params.request_id ?? "")
    ? `${url}?request_id=${params.request_id}`
    : url;
}

export function rescheduleLoginUrl(entry: AuthReturnEntry, requestId?: string): string {
  const query = entry === "reschedule_requests" && UUID.test(requestId ?? "")
    ? `&request_id=${requestId}`
    : "";
  return `/pages/auth/index?return_to=${entry}${query}`;
}

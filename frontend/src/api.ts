let csrf = "";
let sessionVersion = 0;
export const getSessionVersion = () => sessionVersion;
export const setCsrf = (value: string) => {
  const changed = csrf !== value;
  if (changed) sessionVersion += 1;
  csrf = value;
  if (changed) window.dispatchEvent(new Event("session-changed"));
};
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function api<T = any>(
  path: string,
  method = "GET",
  data?: unknown,
  options: { signal?: AbortSignal; keepalive?: boolean; binary?: boolean } = {},
): Promise<T> {
  const startedSession = sessionVersion;
  const response = await fetch("/api/v1" + path, {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      "Content-Type": options.binary
        ? "application/octet-stream"
        : "application/json",
      "X-CSRF-Token": csrf,
      "X-Login-Request": "1",
    },
    body:
      data === undefined
        ? undefined
        : options.binary
          ? (data as Blob)
          : JSON.stringify(data),
    signal: options.signal,
    keepalive: options.keepalive,
  }).catch((e) => {
    if (e.name === "AbortError") throw e;
    throw new Error("暂时无法连接服务，请检查网络后重试");
  });
  const body = await response
    .json()
    .catch(() => ({ detail: "服务暂时不可用，请稍后重试" }));
  if (!response.ok) {
    if (response.status === 401 && startedSession === sessionVersion)
      window.dispatchEvent(new Event("session-expired"));
    const detail = body.detail;
    throw new ApiError(
      typeof detail === "string"
        ? detail
        : detail?.message || "操作失败，请检查输入并重试",
      response.status,
    );
  }
  return body as T;
}
export type Person = {
  id: string;
  username: string;
  display_name: string;
  role: "admin" | "user";
  must_change_password: boolean;
  claim_limit: number | null;
  effective_claim_limit: number;
  claims_used: number;
  group_ids: string[];
};
export type Group = { id: string; name: string; user_ids: string[] };
export type Account = {
  id: string;
  email: string;
  mail_tool: string | null;
  mail_tool_name: string | null;
  tier: string;
  tier_name?: string;
  disabled: boolean;
  created_at: string;
  expires_at: string | null;
  capacity: number | null;
  effective_capacity: number;
  quota: number | null;
  quota_depleted: boolean;
  reset_at: string | null;
  quota_reset_interval_days: number | null;
  quota_updated_at: string | null;
  quota_source: string | null;
  health: string;
  health_categories: string[];
  health_category_names?: string[];
  health_note: string;
  health_version: number;
  can_claim: boolean;
  has_open_claim: boolean;
  blocked_reasons: string[];
  active_count: number;
  current_users: string[];
  recent_updates: Audit[];
  group_ids?: string[];
  user_ids?: string[];
};
export type Claim = {
  id: string;
  claimed_at: string;
  returned_at: string | null;
  return_kind: string | null;
  invalidated_at: string | null;
  invalidation_kind: string | null;
  invalidation_reason: string | null;
  account: Account | null;
  user_id?: string;
  user_name?: string;
  account_id?: string;
  account_email?: string;
};
export type Audit = {
  id: number;
  created_at: string;
  actor: string;
  kind: string;
  details: Record<string, any>;
  account_id: string | null;
  target_user_id: string | null;
  account_email?: string | null;
  target_username?: string | null;
  target_display_name?: string | null;
};
export type AccountOption = { id: string; name: string; enabled: boolean };
export type AnomalyCategory = AccountOption & { cooldown_hours: number | null };
export type AccountOptions = {
  tiers: AccountOption[];
  anomaly_categories: AnomalyCategory[];
};
export type Settings = {
  user_claim_limit: number;
  account_capacity: number;
  observation_hours: number;
  cooldown_hours: number;
  account_options: AccountOptions;
  session_days: number;
  quota_depleted_threshold: number;
  email_code_timeout_minutes: number;
  quota_reset_interval_days: number;
};

import type { Account } from "./api";

export const ACCOUNT_STATUSES = [
  { value: "abnormal", label: "异常" },
  { value: "possibly_recovered", label: "可能恢复" },
  { value: "empty", label: "额度已耗尽" },
  { value: "full", label: "人数已满" },
  { value: "normal", label: "正常" },
] as const;
export const DISABLED_ACCOUNT_STATUS = {
  value: "disabled",
  label: "已停用",
} as const;
export type AccountStatus =
  | (typeof ACCOUNT_STATUSES)[number]["value"]
  | (typeof DISABLED_ACCOUNT_STATUS)["value"];
export const ALL_ACCOUNT_STATUSES: AccountStatus[] = ACCOUNT_STATUSES.map(
  (s) => s.value,
);
export const ALL_ADMIN_ACCOUNT_STATUSES: AccountStatus[] = [
  ...ALL_ACCOUNT_STATUSES,
  DISABLED_ACCOUNT_STATUS.value,
];

export function accountStatus(a: Account): AccountStatus {
  if (a.disabled) return DISABLED_ACCOUNT_STATUS.value;
  if (a.health === "abnormal" || a.health === "possibly_recovered")
    return a.health;
  if (a.quota_depleted) return "empty";
  if (a.active_count >= a.effective_capacity) return "full";
  return "normal";
}

export function isInAccountHall(a: Account, time = Date.now()) {
  return (
    !a.disabled && (!a.expires_at || new Date(a.expires_at).getTime() > time)
  );
}

export type AccountSort = {
  key: "email" | "quota" | "reset_at";
  order: "ascend" | "descend";
};

const names = new Intl.Collator("en", { numeric: true, sensitivity: "base" });

export function compareAccounts(a: Account, b: Account, sort: AccountSort) {
  const byName = names.compare(a.email, b.email);
  const direction = sort.order === "ascend" ? 1 : -1;
  if (sort.key === "email") return byName * direction;
  if (sort.key === "reset_at") {
    // For near-to-far ordering, accounts without a scheduled reset come first;
    // for far-to-near ordering, they stay at the end.
    if (a.reset_at === null || b.reset_at === null) {
      if (a.reset_at === b.reset_at) return byName;
      return a.reset_at === null ? -direction : direction;
    }
    return (
      (new Date(a.reset_at).getTime() - new Date(b.reset_at).getTime()) *
        direction || byName
    );
  }
  // Unknown quota stays last in both directions; equal values sort by name.
  if (a.quota === null || b.quota === null) {
    if (a.quota === b.quota) return byName;
    return a.quota === null ? 1 : -1;
  }
  return (a.quota - b.quota) * direction || byName;
}

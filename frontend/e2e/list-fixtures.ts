import type { Route } from "@playwright/test";
import type { Account } from "../src/api";
import {
  accountStatus,
  compareAccounts,
  isInAccountHall,
} from "../src/accountList";

// A fixture HTTP server: UI tests receive only the requested page, as a real API
// would. Backend tests independently exercise the SQL filters and query budgets.
export async function fulfillApi(
  route: Route,
  response: NonNullable<Parameters<Route["fulfill"]>[0]>,
) {
  let data = response.json;
  if (data === undefined && typeof response.body === "string") {
    try {
      data = JSON.parse(response.body);
    } catch {
      /* e.g. CSV exports */
    }
  }
  if (!Array.isArray(data) || route.request().method() !== "GET")
    return route.fulfill(response);
  const url = new URL(route.request().url());
  const params = url.searchParams;
  let items = [...data];
  if (url.pathname === "/api/v1/accounts") {
    const statuses = params.get("statuses");
    items = items.filter(
      (a: Account) =>
        (params.get("scope") !== "hall" || isInAccountHall(a)) &&
        a.email.toLowerCase().includes((params.get("q") || "").toLowerCase()) &&
        (!params.has("tier") || a.tier === params.get("tier")) &&
        (!params.has("group_id") ||
          a.group_ids?.includes(params.get("group_id")!)) &&
        (statuses === null || statuses.split(",").includes(accountStatus(a))),
    );
    const key = params.get("sort_by");
    if (
      key === "email" ||
      key === "quota" ||
      key === "reset_at" ||
      key === "quota_updated_at" ||
      key === "expires_at"
    )
      items.sort((a, b) =>
        compareAccounts(a, b, {
          key,
          order: params.get("order") === "desc" ? "descend" : "ascend",
        }),
      );
  }
  const total = items.length;
  const offset = Number(params.get("offset") || 0);
  items = items.slice(
    offset,
    params.has("limit") ? offset + Number(params.get("limit")) : undefined,
  );
  return route.fulfill({
    ...response,
    body: undefined,
    json: items,
    headers: { ...response.headers, "X-Total-Count": String(total) },
  });
}

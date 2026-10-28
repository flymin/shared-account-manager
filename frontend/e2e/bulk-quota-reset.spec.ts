import { test, expect, type Page } from "@playwright/test";
import { defaultAccountOptions } from "./option-fixtures";
import { fulfillApi } from "./list-fixtures";

async function fixture(page: Page, admin = true) {
  const submissions: any[] = [];
  const previews: any[] = [];
  const accounts = Array.from({ length: 22 }, (_, i) => ({
    id: `account-${i}`,
    email: `selected${i}@example.test`,
    tier: "5x",
    disabled: false,
    created_at: "2026-01-01T00:00:00Z",
    expires_at: null,
    capacity: 1,
    effective_capacity: 1,
    quota: 20,
    quota_depleted: false,
    reset_at: "2035-01-01T00:00:00Z",
    quota_updated_at: "2026-01-01T00:00:00Z",
    quota_source: "user",
    quota_reset_interval_days: null,
    health: "normal",
    health_categories: [],
    health_note: "",
    health_version: 0,
    active_count: 0,
    current_users: [],
    recent_updates: [],
    note_count: 0,
    group_ids: ["test-group"],
    user_ids: [],
    can_claim: true,
    has_open_claim: false,
    blocked_reasons: [],
  }));
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      expect(body.filters).not.toHaveProperty("offset");
      expect(body.filters).not.toHaveProperty("limit");
      if (path.endsWith("/preview")) {
        previews.push(body);
        return route.fulfill({
          json: { matched_count: 22, eligible_count: 22, skipped_count: 0 },
        });
      }
      submissions.push(body);
      return route.fulfill({
        json: {
          matched_count: 22,
          eligible_count: 21,
          reset_count: 21,
          skipped_count: 1,
        },
      });
    }
    const data =
      path === "/auth/me"
        ? {
            user: {
              id: "viewer",
              username: "viewer",
              display_name: "测试管理员",
              role: admin ? "admin" : "user",
              must_change_password: false,
              effective_claim_limit: 1,
              claims_used: 0,
              group_ids: [],
            },
            csrf_token: "fictional-test-token",
          }
        : path === "/account-options"
          ? defaultAccountOptions
          : path === "/accounts"
            ? accounts
            : path === "/groups"
              ? [{ id: "test-group", name: "测试组", member_ids: [] }]
              : [];
    return fulfillApi(route, { json: data });
  });
  await page.goto("/");
  await expect(page.locator(".app-shell")).toBeVisible();
  if (admin) {
    await (
      (await page.locator(".mobile-nav").isVisible())
        ? page
            .locator(".mobile-nav")
            .getByRole("button", { name: "账号管理", exact: true })
        : page
            .locator(".sidebar")
            .getByRole("menuitem", { name: "账号管理", exact: true })
    ).click();
  }
  return { previews, submissions };
}

test("一键重置沿用当前筛选，跨页预览，确认使用同一实际时间", async ({
  page,
}) => {
  await page.addInitScript(() =>
    Object.defineProperty(crypto, "randomUUID", { value: undefined }),
  );
  const { previews, submissions } = await fixture(page);
  await page.getByRole("textbox", { name: "搜索账号邮箱" }).fill("selected");
  await expect(page.locator(".list-caption strong")).toHaveText("22");
  await page
    .locator(".account-pagination")
    .getByTitle("2", { exact: true })
    .click();
  await page.getByRole("button", { name: "一键重置", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "一键重置额度" });
  await expect(dialog).toContainText("全部匹配账号，跨全部页面");
  await expect(
    dialog.getByRole("button", { name: "确认重置", exact: true }),
  ).toBeDisabled();
  await dialog.getByRole("button", { name: "预览范围" }).click();
  await expect(dialog).toContainText("将重置 22 个");
  expect(previews[0].filters.q).toBe("selected");
  expect(previews[0].filters.statuses).toContain("disabled");
  await dialog.getByRole("button", { name: "确认重置", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("已重置 21 个账号，保留 1 个账号")).toBeVisible();
  expect(submissions).toHaveLength(1);
  expect(submissions[0].occurred_at).toBe(previews[0].occurred_at);
  expect(submissions[0].filters).toEqual(previews[0].filters);
  expect(submissions[0].request_id).toMatch(/^[0-9a-f-]{36}$/);
});

test("变更重置时间后必须重新预览", async ({ page }) => {
  await fixture(page);
  await page.getByRole("button", { name: "一键重置", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "一键重置额度" });
  await dialog.getByRole("button", { name: "预览范围" }).click();
  const confirm = dialog.getByRole("button", { name: "确认重置", exact: true });
  await expect(confirm).toBeEnabled();
  await dialog.getByRole("button", { name: "清除日期和时间" }).click();
  await expect(confirm).toBeDisabled();
  await dialog.getByRole("button", { name: "预览范围" }).click();
  await expect(dialog.getByText("请填写实际重置发生时间")).toBeVisible();
});

test("普通用户没有一键重置入口", async ({ page }) => {
  await fixture(page, false);
  await expect(
    page.getByRole("button", { name: "一键重置", exact: true }),
  ).toHaveCount(0);
});

import { test, expect } from "@playwright/test";
import { fulfillApi } from "./list-fixtures";
import { defaultAccountOptions } from "./option-fixtures";

test("用户和组按页加载，组编辑器可选择当前页以外的用户", async ({ page }) => {
  const users = Array.from({ length: 23 }, (_, i) => ({
    id: `user-${i}`,
    username: `fixture${i}`,
    display_name: `Fixture ${i}`,
    role: "user",
    must_change_password: false,
    claim_limit: null,
    effective_claim_limit: 1,
    claims_used: 0,
    group_ids: [],
  }));
  const groups = Array.from({ length: 15 }, (_, i) => ({
    id: `group-${i}`,
    name: `Fixture group ${i}`,
    user_ids: i === 0 ? ["user-22"] : [],
  }));
  const reads: URL[] = [];
  let failGroupPage = false;
  await page.route("**/api/v1/**", async (route) => {
    expect(route.request().method()).toBe("GET");
    const url = new URL(route.request().url());
    reads.push(url);
    const path = url.pathname.replace("/api/v1", "");
    if (
      path === "/groups" &&
      url.searchParams.get("offset") === "10" &&
      failGroupPage
    )
      return route.fulfill({
        status: 500,
        json: { detail: "测试用户组加载失败" },
      });
    const options = url.searchParams.get("view") === "options";
    const json =
      path === "/auth/me"
        ? {
            user: { ...users[0], role: "admin" },
            csrf_token: "fictional-session-token",
          }
        : path === "/account-options"
          ? defaultAccountOptions
          : path === "/users"
            ? options
              ? users.map(({ id, username, display_name }) => ({
                  id,
                  username,
                  display_name,
                }))
              : users
            : path === "/groups"
              ? options
                ? groups.map(({ id, name }) => ({ id, name }))
                : groups
              : [];
    await fulfillApi(route, { json });
  });
  await page.goto("/");
  await expect(page.locator(".app-shell")).toBeVisible();
  const mobile = await page.locator(".mobile-nav").isVisible();
  await (
    mobile
      ? page
          .locator(".mobile-nav")
          .getByRole("button", { name: "用户与用户组" })
      : page.locator(".sidebar").getByRole("menuitem", { name: "用户与用户组" })
  ).click();
  await expect(
    page.getByRole("tab", { name: "用户 · 23", exact: true }),
  ).toBeVisible();
  const active = page.locator(".ant-tabs-tabpane-active");
  await expect(active.locator("tr.ant-table-row")).toHaveCount(10);
  expect(
    reads.some(
      (url) =>
        url.pathname === "/api/v1/users" &&
        url.searchParams.get("view") === "options",
    ),
  ).toBe(false);
  await active.locator('.ant-pagination-item[title="3"]').click();
  await expect(active.locator("tr.ant-table-row")).toHaveCount(3);
  await expect(active.getByText("Fixture 22", { exact: true })).toBeVisible();
  expect(
    reads.some(
      (url) =>
        url.pathname === "/api/v1/users" &&
        url.searchParams.get("offset") === "20" &&
        url.searchParams.get("limit") === "10",
    ),
  ).toBe(true);
  await page.getByRole("tab", { name: "用户组 · 15", exact: true }).click();
  await expect(active.locator("tr.ant-table-row")).toHaveCount(10);
  failGroupPage = true;
  await active.locator(".ant-pagination-next").click();
  await expect(
    page.getByText("测试用户组加载失败", { exact: true }),
  ).toBeVisible();
  await expect(active.locator(".ant-spin-spinning")).toHaveCount(0);
  failGroupPage = false;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(
    page.getByText("测试用户组加载失败", { exact: true }),
  ).toHaveCount(0);
  await expect(active.locator("tr.ant-table-row")).toHaveCount(5);
  await active.locator('.ant-pagination-item[title="1"]').click();
  await expect(active.locator("tr.ant-table-row")).toHaveCount(10);
  await active
    .getByRole("button", { name: "编辑", exact: true })
    .first()
    .click();
  const dialog = page.getByRole("dialog", { name: "编辑用户组" });
  await expect(
    dialog.getByText("Fixture 22（fixture22）", { exact: true }),
  ).toBeVisible();
  await dialog.getByLabel("组成员").click();
  await dialog.getByLabel("组成员").fill("fixture21");
  await expect(
    page
      .locator(".ant-select-dropdown:visible")
      .getByText("Fixture 21（fixture21）", { exact: true }),
  ).toBeVisible();
});

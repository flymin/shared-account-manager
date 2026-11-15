import { test, expect, type Page } from "@playwright/test";
import { fulfillApi } from "./list-fixtures";
import { defaultAccountOptions } from "./option-fixtures";
import { matchesAccountStatuses } from "../src/accountList";

async function openFixture(page: Page) {
  const state = {
    conflict: false,
    selections: [] as any[],
    previews: [] as any[],
    submissions: [] as any[],
    accounts: Array.from({ length: 26 }, (_, i) => ({
      id: `account-${i}`,
      email: `member${i}@example.test`,
      tier: i % 2 ? "20x" : "5x",
      disabled: i === 0,
      created_at: "2026-01-01T00:00:00Z",
      expires_at: null,
      capacity: null,
      effective_capacity: 2,
      quota: 70,
      quota_depleted: false,
      reset_at: "2035-01-01T00:00:00Z",
      quota_updated_at: null,
      quota_reset_interval_days: null,
      quota_source: null,
      health: "normal",
      health_categories: [],
      health_note: "",
      health_version: 0,
      active_count: 0,
      current_users: [],
      recent_updates: [],
      note_count: 0,
      group_ids: [i < 10 ? "group-a" : "group-b"],
      user_ids: [],
      can_claim: true,
      has_open_claim: false,
      blocked_reasons: [],
    })),
  };
  function filtered(filters: any) {
    return state.accounts.filter(
      (a) =>
        a.email.includes(filters.q || "") &&
        (!filters.tier || a.tier === filters.tier) &&
        (!filters.group_id || a.group_ids.includes(filters.group_id)) &&
        matchesAccountStatuses(a as any, filters.statuses ?? null),
    );
  }
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      if (path === "/account-selections") {
        state.selections.push(body);
        const ids = filtered(body.filters)
          .map((a) => a.id)
          .filter((id) => !body.account_ids || body.account_ids.includes(id));
        return route.fulfill({ json: { account_ids: ids, count: ids.length } });
      }
      if (path.endsWith("/preview")) {
        state.previews.push(body);
        if (
          body.account_ids.some(
            (id: string) => !filtered(body.filters).some((a) => a.id === id),
          )
        )
          return route.fulfill({
            status: 409,
            json: { detail: "部分账号不再符合筛选，请重新核对选择并预览" },
          });
        return route.fulfill({
          json: {
            revision: "a".repeat(64),
            count: body.account_ids.length,
            fields: Object.keys(body.changes),
          },
        });
      }
      state.submissions.push(body);
      if (state.conflict)
        return route.fulfill({
          status: 409,
          json: { detail: "账号数据已发生变化，请重新预览" },
        });
      return route.fulfill({
        json: {
          count: body.account_ids.length,
          fields: Object.keys(body.changes),
        },
      });
    }
    const data =
      path === "/auth/me"
        ? {
            user: {
              id: "admin",
              username: "admin",
              display_name: "测试管理员",
              role: "admin",
              must_change_password: false,
              effective_claim_limit: 2,
              claims_used: 0,
              group_ids: [],
            },
            csrf_token: "fictional-batch-token",
          }
        : path === "/account-options"
          ? defaultAccountOptions
          : path === "/accounts"
            ? state.accounts
            : path === "/groups"
              ? [
                  { id: "group-a", name: "研发组" },
                  { id: "group-b", name: "测试组" },
                ]
              : path === "/users"
                ? [
                    {
                      id: "member",
                      username: "member",
                      display_name: "测试成员",
                    },
                  ]
                : path === "/mail-tools"
                  ? {
                      default: "mailcom",
                      items: [{ id: "mailcom", name: "邮件工具" }],
                    }
                  : [];
    return fulfillApi(route, { json: data });
  });
  await page.goto("/");
  await expect(page.locator(".app-shell")).toBeVisible();
  await (
    (await page.locator(".mobile-nav").isVisible())
      ? page
          .locator(".mobile-nav")
          .getByRole("button", { name: "账号管理", exact: true })
      : page
          .locator(".sidebar")
          .getByRole("menuitem", { name: "账号管理", exact: true })
  ).click();
  await expect(page.locator(".list-caption strong")).toHaveText("26");
  return state;
}
async function enter(page: Page) {
  await page.getByRole("button", { name: "多选", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "取消多选", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "批量编辑", exact: true }),
  ).toBeDisabled();
}
async function all(page: Page) {
  await page.getByRole("button", { name: "全部选择", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "批量编辑", exact: true }),
  ).toBeEnabled();
}

test("多选按需展开，全选跨页且实际勾选决定提交范围", async ({ page }) => {
  const state = await openFixture(page);
  await expect(
    page.getByRole("button", { name: "全部选择", exact: true }),
  ).toHaveCount(0);
  await enter(page);
  await expect(
    page.getByRole("button", { name: "全部取消", exact: true }),
  ).toBeDisabled();
  await all(page);
  await expect(page.locator(".selection-count")).toHaveText("已选 26");
  await expect(
    page.getByRole("button", { name: "全部选择", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("checkbox", {
      name: "选择账号 member0@example.test",
      exact: true,
    })
    .click();
  await expect(page.locator(".selection-count")).toHaveText("已选 25");
  await page
    .locator(".account-pagination")
    .getByTitle("2", { exact: true })
    .click();
  await expect(page.locator(".selection-count")).toHaveText("已选 25");
  await page.getByRole("button", { name: "批量编辑", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "批量编辑账号" });
  await expect(dialog).toContainText("已选择 25 个账号");
  await dialog
    .getByRole("switch", { name: "修改同时领用人数", exact: true })
    .click();
  await dialog.getByRole("spinbutton").fill("3");
  await dialog
    .getByRole("switch", { name: "修改追加备注", exact: true })
    .click();
  await dialog.getByRole("textbox").fill("批量补充情况");
  await dialog.getByRole("button", { name: "预览修改", exact: true }).click();
  await expect(dialog).toContainText("将修改 25 个账号");
  expect(state.previews[0].account_ids).toHaveLength(25);
  expect(state.previews[0].account_ids).not.toContain("account-0");
  expect(state.previews[0].changes).toEqual({
    capacity: 3,
    note: "批量补充情况",
  });
  await dialog.getByRole("button", { name: "确认修改", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(
    page.getByRole("button", { name: "多选", exact: true }),
  ).toBeVisible();
  expect(state.submissions).toHaveLength(1);
});

test("全部取消保持多选，取消多选按钮退出；筛选只保留匹配的选择", async ({
  page,
}) => {
  await openFixture(page);
  await enter(page);
  await all(page);
  await page.getByRole("button", { name: "全部取消", exact: true }).click();
  await expect(page.locator(".selection-count")).toHaveText("已选 0");
  await expect(
    page.getByRole("button", { name: "批量编辑", exact: true }),
  ).toBeDisabled();
  await all(page);
  await page.getByRole("textbox", { name: "搜索账号邮箱" }).fill("member1@");
  await expect(page.locator(".selection-count")).toHaveText("已选 1");
  await page.getByRole("textbox", { name: "搜索账号邮箱" }).fill("");
  await expect(page.locator(".list-caption strong")).toHaveText("26");
  await expect(page.locator(".selection-count")).toHaveText("已选 1");
  await page.getByRole("button", { name: "取消多选", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "多选", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "全部取消", exact: true }),
  ).toHaveCount(0);
});

test("冲突保留表单并要求重新预览，未启用字段不会提交", async ({ page }) => {
  const state = await openFixture(page);
  await enter(page);
  await all(page);
  await page.getByRole("button", { name: "批量编辑", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "批量编辑账号" });
  await dialog
    .getByRole("switch", { name: "修改可见用户组", exact: true })
    .click();
  await dialog.getByRole("button", { name: "预览修改", exact: true }).click();
  await expect(
    dialog.getByRole("button", { name: "确认修改", exact: true }),
  ).toBeEnabled();
  state.conflict = true;
  await dialog.getByRole("button", { name: "确认修改", exact: true }).click();
  await expect(dialog).toContainText("账号数据已发生变化");
  await expect(
    dialog.getByRole("button", { name: "确认修改", exact: true }),
  ).toBeDisabled();
  expect(state.submissions[0].changes).toEqual({ group_ids: [] });
  state.conflict = false;
  await dialog.getByRole("button", { name: "预览修改", exact: true }).click();
  await expect(
    dialog.getByRole("button", { name: "确认修改", exact: true }),
  ).toBeEnabled();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator(".selection-count")).toHaveText("已选 26");
});

test("桌面与移动端多选和编辑界面布局清晰，不横向溢出", async ({
  page,
}, info) => {
  await openFixture(page);
  const mobile = await page.locator(".mobile-nav").isVisible();
  for (const width of mobile ? [390, 360] : [1440, 1280]) {
    await page.setViewportSize({ width, height: mobile ? 844 : 1000 });
    await page.screenshot({
      path: info.outputPath(`default-${width}.png`),
      fullPage: false,
      animations: "disabled",
    });
    await enter(page);
    await all(page);
    await expect(
      page.getByRole("button", { name: "全部取消", exact: true }),
    ).toBeEnabled();
    await page.screenshot({
      path: info.outputPath(`selected-${width}.png`),
      fullPage: false,
      animations: "disabled",
    });
    await page.getByRole("button", { name: "批量编辑", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "批量编辑账号" });
    await dialog
      .getByRole("switch", { name: "修改同时领用人数", exact: true })
      .click();
    await dialog.getByRole("spinbutton").fill("4");
    await dialog.getByRole("button", { name: "预览修改", exact: true }).click();
    await expect(dialog).toContainText("将修改 26 个账号");
    await expect(
      dialog.getByRole("button", { name: "确认修改", exact: true }),
    ).toBeEnabled();
    await page.screenshot({
      path: info.outputPath(`preview-${width}.png`),
      fullPage: false,
      animations: "disabled",
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 1,
      ),
    ).toBe(false);
    const bounds = await dialog.boundingBox();
    expect(bounds!.width).toBeLessThanOrEqual(width);
    await dialog.getByRole("button", { name: "取消", exact: true }).click();
    await page.getByRole("button", { name: "取消多选", exact: true }).click();
  }
});

test("冲突移除失效账号并保留修改内容，可重新预览", async ({ page }) => {
  const state = await openFixture(page);
  await enter(page);
  await all(page);
  await page.getByRole("button", { name: "批量编辑", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "批量编辑账号" });
  await dialog
    .getByRole("switch", { name: "修改同时领用人数", exact: true })
    .click();
  await dialog.getByRole("spinbutton").fill("6");
  await dialog.getByRole("button", { name: "预览修改", exact: true }).click();
  await expect(
    dialog.getByRole("button", { name: "确认修改", exact: true }),
  ).toBeEnabled();
  state.accounts.shift();
  state.conflict = true;
  await dialog.getByRole("button", { name: "确认修改", exact: true }).click();
  await expect(dialog).toContainText("已移除 1 个不再匹配的账号");
  await expect(dialog).toContainText("已选择 25 个账号");
  await expect(dialog.getByRole("spinbutton")).toHaveValue("6");
  state.conflict = false;
  await dialog.getByRole("button", { name: "预览修改", exact: true }).click();
  await expect(dialog).toContainText("将修改 25 个账号");
});

test("首次预览前账号失效也能保留表单并核对新范围", async ({ page }) => {
  const state = await openFixture(page);
  await enter(page);
  await all(page);
  await page.getByRole("button", { name: "批量编辑", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "批量编辑账号" });
  await dialog
    .getByRole("switch", { name: "修改同时领用人数", exact: true })
    .click();
  await dialog.getByRole("spinbutton").fill("6");
  state.accounts.shift();
  await dialog.getByRole("button", { name: "预览修改", exact: true }).click();
  await expect(dialog).toContainText("已移除 1 个不再匹配的账号");
  await expect(dialog.getByRole("spinbutton")).toHaveValue("6");
  await dialog.getByRole("button", { name: "预览修改", exact: true }).click();
  await expect(dialog).toContainText("将修改 25 个账号");
});

test("未选账号也能直接取消多选，批量操作一起隐藏", async ({ page }) => {
  await openFixture(page);
  await expect(
    page.getByRole("button", { name: "批量编辑", exact: true }),
  ).toHaveCount(0);
  await enter(page);
  await expect(
    page.getByRole("button", { name: "全部选择", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "取消多选", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "多选", exact: true }),
  ).toBeVisible();
  for (const name of ["取消多选", "批量编辑", "全部选择", "全部取消"]) {
    await expect(page.getByRole("button", { name, exact: true })).toHaveCount(
      0,
    );
  }
  await expect(
    page.getByRole("checkbox", {
      name: "选择账号 member0@example.test",
      exact: true,
    }),
  ).toHaveCount(0);
});

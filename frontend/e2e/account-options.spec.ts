import { fulfillApi } from "./list-fixtures";
import { test, expect, type Page } from "@playwright/test";
import type { Settings } from "../src/api";

async function nav(page: Page, name: string) {
  const mobile = page.locator(".mobile-nav");
  await (
    (await mobile.isVisible())
      ? mobile.getByRole("button", { name, exact: true })
      : page.locator(".sidebar").getByRole("menuitem", { name, exact: true })
  ).click();
}

async function fixture(page: Page, admin = true, includeRetired = false) {
  const state = {
    cfg: {
      user_claim_limit: 1,
      account_capacity: 1,
      observation_hours: 4,
      cooldown_hours: 24,
      session_days: 7,
      email_code_timeout_minutes: 5,
      quota_depleted_threshold: 5,
      quota_reset_interval_days: 7,
      account_options: {
        tiers: [
          { id: "standard", name: "标准档", enabled: true },
          { id: "legacy", name: "旧档位", enabled: false },
        ],
        anomaly_categories: [
          { id: "slow", name: "响应延迟", enabled: true, cooldown_hours: 48 },
          {
            id: "unavailable",
            name: "暂时不可用",
            enabled: true,
            cooldown_hours: null,
          },
          {
            id: "retired",
            name: "停用类别",
            enabled: false,
            cooldown_hours: 12,
          },
        ],
      },
    } as Settings,
    imports: [] as any[],
    reports: [] as any[],
    edits: [] as any[],
    optionsReads: 0,
    activeByTier: { standard: 1, legacy: 0 } as Record<string, number>,
    usageReads: 0,
    usageFailure: false,
    settingsWrites: 0,
  };
  function account(tier = "standard") {
    return {
      id: tier === "standard" ? "example-account" : "retired-account",
      email:
        tier === "standard" ? "example@example.test" : "retired@example.test",
      tier,
      tier_name: state.cfg.account_options.tiers.find((t) => t.id === tier)
        ?.name,
      mail_tool: null,
      mail_tool_name: null,
      disabled: false,
      created_at: "2026-01-01T00:00:00Z",
      expires_at: null,
      capacity: null,
      effective_capacity: 1,
      quota: 80,
      quota_depleted: false,
      reset_at: null,
      quota_reset_interval_days: null,
      health: "normal",
      health_categories: [],
      health_category_names: [],
      health_note: "",
      health_version: 0,
      can_claim: true,
      has_open_claim: false,
      blocked_reasons: [],
      active_count: 0,
      current_users: [],
      recent_updates: [],
      group_ids: [],
      user_ids: [],
    };
  }
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    const method = request.method();
    const send = (json: unknown) => fulfillApi(route, { json });
    if (path === "/auth/me")
      return send({
        user: {
          id: "viewer",
          username: "viewer",
          display_name: "测试用户",
          role: admin ? "admin" : "user",
          must_change_password: false,
          claim_limit: 1,
          effective_claim_limit: 1,
          claims_used: 0,
          group_ids: [],
        },
        csrf_token: "fictional-session",
      });
    if (path === "/account-options") {
      state.optionsReads += 1;
      return send({
        ...state.cfg.account_options,
        cooldown_hours: state.cfg.cooldown_hours,
      });
    }
    if (path === "/settings") {
      if (method === "PUT") {
        state.settingsWrites += 1;
        state.cfg = request.postDataJSON();
      }
      return send(state.cfg);
    }
    if (path === "/account-options/usage") {
      state.usageReads += 1;
      if (state.usageFailure)
        return route.fulfill({
          status: 503,
          json: { detail: "活动账号检查暂不可用" },
        });
      return send({ active_by_tier: state.activeByTier });
    }
    if (path === "/accounts") {
      const scope = new URL(request.url()).searchParams.get("scope");
      const accounts = [
        account(),
        ...(includeRetired ? [account("legacy")] : []),
      ];
      return send(
        accounts.filter(
          (a) =>
            scope !== "hall" ||
            state.cfg.account_options.tiers.some(
              (t) => t.id === a.tier && t.enabled,
            ),
        ),
      );
    }
    if (path === "/accounts/example-account" && method === "GET")
      return send(account());
    if (path === "/mail-tools") return send({ default: null, items: [] });
    if (path === "/account-imports/preview") {
      const data = request.postDataJSON();
      return send({
        rows: [
          {
            line: 1,
            email: "new@example.test",
            tier: data.tier,
            tier_name: state.cfg.account_options.tiers.find(
              (t) => t.id === data.tier,
            )?.name,
          },
        ],
        errors: [],
      });
    }
    if (path === "/account-imports") {
      state.imports.push(request.postDataJSON());
      return send({ count: 1 });
    }
    if (path.endsWith("/health-reports")) {
      state.reports.push(request.postDataJSON());
      return send({ ok: true });
    }
    if (path === "/accounts/example-account" && method === "PATCH") {
      state.edits.push(request.postDataJSON());
      return send(account());
    }
    if (path === "/overview") return send({});
    if (["/groups", "/users", "/claims"].includes(path)) return send([]);
    throw new Error(`Unexpected mocked API request: ${method} ${path}`);
  });
  await page.goto("/");
  await expect(page.locator(".app-shell")).toBeVisible();
  await expect(page.locator(".list-caption strong")).toHaveText("1");
  return state;
}

test("在线维护档位和异常间隔，导入与编辑使用已保存的配置", async ({ page }) => {
  await page.addInitScript(() =>
    Object.defineProperty(crypto, "randomUUID", { value: undefined }),
  );
  const state = await fixture(page);
  await nav(page, "系统设置");
  const tiers = page.getByRole("region", { name: "账号类型配置" });
  const categories = page.getByRole("region", { name: "异常类别配置" });
  await tiers
    .getByLabel("账号类型名称", { exact: true })
    .first()
    .fill("通用档位");
  await tiers
    .getByRole("button", { name: "新增账号类型", exact: true })
    .click();
  await tiers
    .getByLabel("账号类型名称", { exact: true })
    .last()
    .fill("扩展档位");
  await categories
    .getByLabel("恢复间隔（小时）", { exact: true })
    .first()
    .fill("72");
  await categories
    .getByRole("button", { name: "新增异常类别", exact: true })
    .click();
  await categories
    .getByLabel("异常类别名称", { exact: true })
    .last()
    .fill("新增异常");
  await expect(
    categories.getByLabel("恢复间隔（小时）", { exact: true }).last(),
  ).toHaveValue("");
  await categories
    .getByRole("button", { name: "删除异常类别 停用类别", exact: true })
    .click();
  // Compact mobile rows remain inside the viewport and give names their own line.
  const fits = await page.locator(".options-editor").evaluateAll((elements) =>
    elements.every((e) => {
      const rect = e.getBoundingClientRect();
      return (
        rect.left >= 0 &&
        rect.right <= window.innerWidth &&
        e.scrollWidth <= e.clientWidth + 1
      );
    }),
  );
  expect(fits).toBe(true);
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect.poll(() => state.cfg.account_options.tiers.length).toBe(3);
  expect(state.cfg.account_options.tiers[0]).toEqual({
    id: "standard",
    name: "通用档位",
    enabled: true,
  });
  expect(state.cfg.account_options.anomaly_categories[0].cooldown_hours).toBe(
    72,
  );
  expect(
    state.cfg.account_options.anomaly_categories.at(-1)?.cooldown_hours,
  ).toBeNull();
  const customId = state.cfg.account_options.tiers[2].id;
  expect(customId).toBeTruthy();
  await nav(page, "账号管理");
  await page.getByRole("button", { name: "批量登记账号", exact: true }).click();
  await page
    .getByLabel("账号内容", { exact: true })
    .fill("new@example.test----fictional----mailbox");
  await page.getByLabel("本批账号类型", { exact: true }).press("ArrowDown");
  await expect(
    page
      .locator(".ant-select-dropdown:visible")
      .getByText("旧档位", { exact: true }),
  ).toHaveCount(0);
  await page
    .locator(".ant-select-item-option-content")
    .getByText("扩展档位", { exact: true })
    .click();
  await page.getByRole("button", { name: "检查并预览", exact: true }).click();
  await expect(page.locator(".import-preview")).toContainText("扩展档位");
  await page.getByRole("button", { name: "确认整批导入", exact: true }).click();
  await expect.poll(() => state.imports.length).toBe(1);
  expect(state.imports[0].tier).toBe(customId);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("账号类型", { exact: true }).press("ArrowDown");
  await page
    .locator(".ant-select-item-option-content")
    .getByText("扩展档位", { exact: true })
    .click();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => state.edits.length).toBe(1);
  expect(state.edits[0].tier).toBe(customId);
});

test("异常反馈使用动态多选类别，页面聚焦同步运行中的配置修改", async ({
  page,
}) => {
  const state = await fixture(page);
  await nav(page, "账号管理");
  await page.getByRole("button", { name: "异常", exact: true }).click();
  await page.getByLabel("异常类别", { exact: true }).click();
  const dropdown = page.locator(".ant-select-dropdown:visible");
  await expect(dropdown.getByText("停用类别", { exact: true })).toHaveCount(0);
  await dropdown
    .locator(".ant-select-item-option-content")
    .getByText("响应延迟", { exact: true })
    .click();
  await dropdown
    .locator(".ant-select-item-option-content")
    .getByText("暂时不可用", { exact: true })
    .click();
  await page.getByLabel("异常描述", { exact: true }).fill("附加说明");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => state.reports.length).toBe(1);
  expect(state.reports[0].categories).toEqual(["slow", "unavailable"]);
  state.cfg.account_options.anomaly_categories = [
    { id: "new", name: "新类别", enabled: true, cooldown_hours: 6 },
  ];
  state.cfg.account_options.tiers[0].name = "实时档位";
  const reads = state.optionsReads;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => state.optionsReads).toBeGreaterThan(reads);
  await page.getByRole("button", { name: "异常", exact: true }).click();
  await page.getByLabel("异常类别", { exact: true }).click();
  await expect(dropdown.getByText("响应延迟", { exact: true })).toHaveCount(0);
  await dropdown
    .locator(".ant-select-item-option-content")
    .getByText("新类别", { exact: true })
    .click();
  await page.getByLabel("异常描述", { exact: true }).click();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => state.reports.length).toBe(2);
  expect(state.reports[1].categories).toEqual(["new"]);
});

test("普通用户仅筛选启用类型，不能进入配置页", async ({ page }) => {
  await fixture(page, false);
  const navArea = (await page.locator(".mobile-nav").isVisible())
    ? page.locator(".mobile-nav")
    : page.locator(".sidebar");
  await expect(navArea.getByText("系统设置", { exact: true })).toHaveCount(0);
  await page
    .locator(".filter-bar")
    .getByText("全部类型", { exact: true })
    .click();
  await page
    .locator(".ant-select-item-option-content")
    .getByText("标准档", { exact: true })
    .click();
  await expect(page.locator(".list-caption strong")).toHaveText("1");
  await page
    .locator(".filter-bar")
    .getByTitle("标准档", { exact: true })
    .click();
  await expect(
    page
      .locator(".ant-select-dropdown:visible")
      .getByText("旧档位（已停用）", { exact: true }),
  ).toHaveCount(0);
});

test("大厅隐藏停用类型及账号，管理员账号管理保留类型筛选", async ({ page }) => {
  await fixture(page, true, true);
  await expect(page.locator(".list-caption strong")).toHaveText("1");
  await expect(
    page
      .getByText("retired@example.test", { exact: true })
      .filter({ visible: true }),
  ).toHaveCount(0);
  await page
    .getByRole("combobox", { name: "筛选类型", exact: true })
    .press("ArrowDown");
  const dropdown = page.locator(".ant-select-dropdown:visible");
  await expect(
    dropdown.getByText("旧档位（已停用）", { exact: true }),
  ).toHaveCount(0);
  await expect
    .poll(() =>
      dropdown.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return (
          getComputedStyle(element).opacity === "1" &&
          rect.width > 100 &&
          rect.height > 20 &&
          rect.x >= 0 &&
          rect.right <= innerWidth
        );
      }),
    )
    .toBe(true);
  const bounds = await dropdown.boundingBox();
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(
    page.viewportSize()!.width,
  );
  await page.screenshot({
    path: test.info().outputPath("enabled-hall-types.png"),
    animations: "disabled",
  });
  await page
    .getByRole("combobox", { name: "筛选类型", exact: true })
    .press("Escape");
  await nav(page, "账号管理");
  await expect(page.locator(".list-caption strong")).toHaveText("2");
  await expect(
    page
      .getByText("retired@example.test", { exact: true })
      .filter({ visible: true }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "筛选类型", exact: true })
    .press("ArrowDown");
  await dropdown.getByText("旧档位（已停用）", { exact: true }).click();
  await expect(page.locator(".list-caption strong")).toHaveText("1");
  await expect(
    page
      .getByText("retired@example.test", { exact: true })
      .filter({ visible: true }),
  ).toBeVisible();
});

for (const admin of [false, true]) {
  test(`${admin ? "管理员" : "普通用户"}大厅已选类型停用后恢复全部，重新启用后显示`, async ({
    page,
  }) => {
    const state = await fixture(page, admin, true);
    const select = page.getByRole("combobox", {
      name: "筛选类型",
      exact: true,
    });
    await select.press("ArrowDown");
    await page
      .locator(".ant-select-dropdown:visible")
      .getByText("标准档", { exact: true })
      .click();
    state.cfg.account_options.tiers[0].enabled = false;
    state.cfg.account_options.tiers[1].enabled = true;
    const reads = state.optionsReads;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => state.optionsReads).toBeGreaterThan(reads);
    await expect(
      page.locator(".filter-bar").getByTitle("全部类型", { exact: true }),
    ).toBeVisible();
    await expect(
      page
        .getByText("retired@example.test", { exact: true })
        .filter({ visible: true }),
    ).toBeVisible();
    await expect(
      page
        .getByText("example@example.test", { exact: true })
        .filter({ visible: true }),
    ).toHaveCount(0);
    await select.press("ArrowDown");
    await expect(
      page
        .locator(".ant-select-dropdown:visible")
        .getByText("标准档", { exact: true }),
    ).toHaveCount(0);
    await select.press("Escape");
    state.cfg.account_options.tiers[0].enabled = true;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(page.locator(".list-caption strong")).toHaveText("2");
    await expect(
      page
        .getByText("example@example.test", { exact: true })
        .filter({ visible: true }),
    ).toBeVisible();
  });
}

async function disableStandard(page: Page) {
  await nav(page, "系统设置");
  const region = page.getByRole("region", { name: "账号类型配置" });
  // Retain one enabled type, as required by the existing catalog validation.
  await region.getByRole("switch").nth(1).click();
  await region.getByRole("switch").first().click();
  return region;
}

test("停用提示可返回检查或继续保存，保存前核对最新活动账号数量", async ({
  page,
}) => {
  const state = await fixture(page);
  const region = await disableStandard(page);
  const warning = page
    .getByRole("alert")
    .filter({ hasText: "待停用类型仍有活动账号" });
  await expect(warning).toContainText("标准档：1 个");
  state.activeByTier.standard = 3;
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("标准档：3 个活动账号");
  expect(state.settingsWrites).toBe(0);
  await expect
    .poll(() =>
      dialog.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return (
          getComputedStyle(element).opacity === "1" &&
          rect.width > 100 &&
          rect.height > 100 &&
          rect.x >= 0 &&
          rect.y >= 0 &&
          rect.right <= innerWidth &&
          rect.bottom <= innerHeight
        );
      }),
    )
    .toBe(true);
  await page.screenshot({
    path: test.info().outputPath("type-disable-warning.png"),
    animations: "disabled",
  });
  const bounds = await dialog.boundingBox();
  expect(bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await dialog.getByRole("button", { name: "返回检查", exact: true }).click();
  expect(state.settingsWrites).toBe(0);
  await expect(region.getByRole("switch").first()).not.toBeChecked();
  await expect(warning).toContainText("标准档：3 个");
  state.activeByTier.standard = 4;
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect(dialog).toContainText("标准档：4 个活动账号");
  await dialog
    .getByRole("button", { name: "仍然停用并保存", exact: true })
    .click();
  await expect.poll(() => state.settingsWrites).toBe(1);
  expect(state.cfg.account_options.tiers[0].enabled).toBe(false);
});

test("没有活动账号时直接保存停用，不额外要求确认", async ({ page }) => {
  const state = await fixture(page);
  state.activeByTier.standard = 0;
  await disableStandard(page);
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  await expect.poll(() => state.settingsWrites).toBe(1);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(state.cfg.account_options.tiers[0].enabled).toBe(false);
});

test("检查失败不保存停用，重试成功后仍可确认", async ({ page }) => {
  const state = await fixture(page);
  await disableStandard(page);
  state.usageFailure = true;
  const save = page.getByRole("button", { name: "保存设置", exact: true });
  await save.click();
  await expect(page.locator(".ant-message")).toContainText(
    "活动账号检查暂不可用",
  );
  expect(state.settingsWrites).toBe(0);
  await expect(save).toBeEnabled();
  state.usageFailure = false;
  await save.click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "仍然停用并保存", exact: true })
    .click();
  await expect.poll(() => state.settingsWrites).toBe(1);
});

test("多个待停用类型合并检查并逐项列出活动账号", async ({ page }) => {
  const state = await fixture(page);
  state.cfg.account_options.tiers[1].enabled = true;
  state.cfg.account_options.tiers.push({
    id: "remaining",
    name: "保留类型",
    enabled: true,
  });
  state.activeByTier = { standard: 2, legacy: 3 };
  await nav(page, "系统设置");
  const region = page.getByRole("region", { name: "账号类型配置" });
  await region.getByRole("switch").nth(0).click();
  await region.getByRole("switch").nth(1).click();
  const before = state.usageReads;
  await page.getByRole("button", { name: "保存设置", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("标准档：2 个活动账号");
  await expect(dialog).toContainText("旧档位：3 个活动账号");
  expect(state.usageReads).toBe(before + 1);
  await dialog
    .getByRole("button", { name: "仍然停用并保存", exact: true })
    .click();
  await expect.poll(() => state.settingsWrites).toBe(1);
  expect(state.cfg.account_options.tiers.map((t) => t.enabled)).toEqual([
    false,
    false,
    true,
  ]);
});

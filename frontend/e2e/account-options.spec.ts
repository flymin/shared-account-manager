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

async function fixture(page: Page, admin = true) {
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
  };
  function account() {
    return {
      id: "example-account",
      email: "example@example.test",
      tier: "standard",
      tier_name: state.cfg.account_options.tiers[0].name,
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
    const send = (json: unknown) => route.fulfill({ json });
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
      if (method === "PUT") state.cfg = request.postDataJSON();
      return send(state.cfg);
    }
    if (path === "/accounts") return send([account()]);
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
  const tiers = page.getByRole("region", { name: "账号档位配置" });
  const categories = page.getByRole("region", { name: "异常类别配置" });
  await tiers
    .getByLabel("账号档位名称", { exact: true })
    .first()
    .fill("通用档位");
  await tiers
    .getByRole("button", { name: "新增账号档位", exact: true })
    .click();
  await tiers
    .getByLabel("账号档位名称", { exact: true })
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
  await page.getByLabel("本批账号档位", { exact: true }).press("ArrowDown");
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
  await page.getByLabel("账号档位", { exact: true }).press("ArrowDown");
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

test("普通用户可使用动态档位筛选，不能进入配置页", async ({ page }) => {
  await fixture(page, false);
  const navArea = (await page.locator(".mobile-nav").isVisible())
    ? page.locator(".mobile-nav")
    : page.locator(".sidebar");
  await expect(navArea.getByText("系统设置", { exact: true })).toHaveCount(0);
  await page
    .locator(".filter-bar")
    .getByText("全部档位", { exact: true })
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
  await page
    .locator(".ant-select-item-option-content")
    .getByText("旧档位（已停用）", { exact: true })
    .click();
  await expect(page.locator(".list-caption strong")).toHaveText("0");
});

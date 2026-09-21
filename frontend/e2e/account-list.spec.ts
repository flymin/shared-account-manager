import { test, expect, type Page } from "@playwright/test";
import type { Audit } from "../src/api";

const common = {
  tier: "5x",
  disabled: false,
  created_at: "2026-01-01T00:00:00Z",
  expires_at: null,
  capacity: 1,
  effective_capacity: 1,
  quota: 70,
  reset_at: null as string | null,
  quota_reset_interval_days: null,
  quota_updated_at: null,
  quota_source: null,
  health: "normal",
  health_categories: [],
  health_note: "",
  health_version: 0,
  can_claim: true,
  has_open_claim: false,
  blocked_reasons: [],
  active_count: 0,
  current_users: [],
  recent_updates: [] as Audit[],
  group_ids: [],
  user_ids: [],
};
const accounts = [
  {
    ...common,
    id: "c",
    email: "charlie@example.test",
    quota: 30,
    health: "possibly_recovered",
  },
  { ...common, id: "z", email: "zulu@example.test", quota: null },
  {
    ...common,
    id: "e",
    email: "echo@example.test",
    quota: 20,
    active_count: 1,
    can_claim: false,
    blocked_reasons: ["人数已满"],
  },
  {
    ...common,
    id: "b",
    email: "bravo@example.test",
    quota: 0,
    health: "abnormal",
    active_count: 1,
    can_claim: false,
    blocked_reasons: ["人数已满"],
  },
  { ...common, id: "a", email: "alpha@example.test", quota: 80 },
  { ...common, id: "d", email: "delta@example.test", quota: 0 },
  {
    ...common,
    id: "s",
    email: "stopped@example.test",
    disabled: true,
    can_claim: false,
    blocked_reasons: ["已停用"],
  },
  {
    ...common,
    id: "x",
    email: "expired@example.test",
    expires_at: "2020-01-01T00:00:00Z",
    can_claim: false,
    blocked_reasons: ["已过期"],
  },
];

async function openFixture(
  page: Page,
  personalFull = false,
  fixtures = accounts,
  threshold = 5,
  adminMode = false,
) {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "");
    expect(route.request().method()).toBe("GET");
    const user = {
      id: "viewer",
      username: "viewer",
      display_name: "测试用户",
      role: adminMode ? "admin" : "user",
      must_change_password: false,
      claim_limit: 1,
      effective_claim_limit: 1,
      claims_used: personalFull ? 1 : 0,
      group_ids: [],
    };
    const data =
      path === "/auth/me"
        ? { user, csrf_token: "fictional-token" }
        : path === "/accounts"
          ? fixtures.map((a) => ({
              ...(personalFull
                ? {
                    ...a,
                    can_claim: false,
                    blocked_reasons: ["个人名额已满", ...a.blocked_reasons],
                  }
                : a),
              quota_depleted: a.quota !== null && a.quota < threshold,
            }))
          : [];
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(data),
    });
  });
  await page.goto("/");
  await expect(page.locator(".app-shell")).toBeVisible();
  if (adminMode) {
    const mobile = page.locator(".mobile-nav");
    await (
      (await mobile.isVisible())
        ? mobile.getByRole("button", { name: "账号管理", exact: true })
        : page
            .locator(".sidebar")
            .getByRole("menuitem", { name: "账号管理", exact: true })
    ).click();
  }
  await expect(page.locator(".list-caption strong")).toHaveText(
    String(
      fixtures.filter((a) => adminMode || (!a.disabled && !a.expires_at))
        .length,
    ),
  );
}
async function rows(page: Page) {
  return (await page.locator(".mobile-nav").isVisible())
    ? page
        .locator(".mobile-cards .account-card .card-top > strong")
        .allTextContents()
    : page
        .locator(".desktop-table .ant-table-tbody .account-email")
        .allTextContents();
}
async function sort(
  page: Page,
  field: "email" | "quota" | "reset_at",
  direction: "ascend" | "descend",
) {
  if (await page.locator(".mobile-nav").isVisible()) {
    await page.locator(".mobile-account-sort .ant-select-selector").click();
    const title =
      field === "email"
        ? "账号名"
        : field === "quota"
          ? "剩余额度"
          : "下次重置";
    const suffix =
      field === "reset_at"
        ? direction === "ascend"
          ? "↑ 近到远"
          : "↓ 远到近"
        : direction === "ascend"
          ? "↑ 升序"
          : "↓ 降序";
    const label = `${title} ${suffix}`;
    await page
      .locator(".ant-select-dropdown:visible")
      .getByText(label, { exact: true })
      .click();
  } else {
    const column = page.getByRole("columnheader", {
      name:
        field === "email"
          ? /^账号$/
          : field === "quota"
            ? /^剩余额度$/
            : /^下次重置$/,
    });
    const expected = direction === "ascend" ? "ascending" : "descending";
    for (
      let tries = 0;
      tries < 2 && (await column.getAttribute("aria-sort")) !== expected;
      tries++
    )
      await column.locator(".ant-table-column-sorters").click();
    await expect(column).toHaveAttribute("aria-sort", expected);
  }
}

for (const adminMode of [false, true]) {
  test(`${adminMode ? "账号管理" : "账号大厅"}首次排序方向明确，切换列后重新按默认方向排序`, async ({
    page,
  }) => {
    await openFixture(
      page,
      false,
      accounts.filter((a) => !a.disabled && !a.expires_at),
      5,
      adminMode,
    );
    if (await page.locator(".mobile-nav").isVisible()) {
      await expect(
        page.locator(".mobile-account-sort .ant-select-selection-item"),
      ).toHaveText(adminMode ? "账号名 ↑ 升序" : "剩余额度 ↓ 降序");
      await page.locator(".mobile-account-sort .ant-select-selector").click();
      await expect(
        page.locator(
          ".ant-select-dropdown:visible .ant-select-item-option-content",
        ),
      ).toHaveText([
        "账号名 ↑ 升序",
        "账号名 ↓ 降序",
        "剩余额度 ↓ 降序",
        "剩余额度 ↑ 升序",
        "下次重置 ↑ 近到远",
        "下次重置 ↓ 远到近",
      ]);
      await page
        .locator(".ant-select-dropdown:visible")
        .getByText("剩余额度 ↓ 降序", { exact: true })
        .click();
      await expect
        .poll(() => rows(page))
        .toEqual([
          "alpha@example.test",
          "charlie@example.test",
          "echo@example.test",
          "bravo@example.test",
          "delta@example.test",
          "zulu@example.test",
        ]);
      return;
    }
    const email = page.getByRole("columnheader", { name: /^账号$/ });
    const quota = page.getByRole("columnheader", { name: /^剩余额度$/ });
    const alphabetical = ["alpha", "bravo", "charlie", "delta", "echo", "zulu"];
    await expect
      .poll(() => rows(page))
      .toEqual(
        (adminMode
          ? alphabetical
          : ["alpha", "charlie", "echo", "bravo", "delta", "zulu"]
        ).map((name) => `${name}@example.test`),
      );
    if (!adminMode) {
      await expect(quota).toHaveAttribute("aria-sort", "descending");
      await quota.locator(".ant-table-column-sorters").click();
      await expect(quota).toHaveAttribute("aria-sort", "ascending");
      await expect
        .poll(() => rows(page))
        .toEqual(
          ["bravo", "delta", "echo", "charlie", "alpha", "zulu"].map(
            (name) => `${name}@example.test`,
          ),
        );
    }
    for (const [column, direction, names] of [
      [email, "ascending", alphabetical],
      [email, "descending", [...alphabetical].reverse()],
      [
        quota,
        "descending",
        ["alpha", "charlie", "echo", "bravo", "delta", "zulu"],
      ],
      [
        quota,
        "ascending",
        ["bravo", "delta", "echo", "charlie", "alpha", "zulu"],
      ],
      [email, "ascending", alphabetical],
      [
        quota,
        "descending",
        ["alpha", "charlie", "echo", "bravo", "delta", "zulu"],
      ],
    ] as const) {
      // Exactly one click: retrying until the desired order hides regressions.
      await column.locator(".ant-table-column-sorters").click();
      await expect(column).toHaveAttribute("aria-sort", direction);
      await expect
        .poll(() => rows(page))
        .toEqual(names.map((name) => `${name}@example.test`));
    }
  });
}

test("大厅默认按额度降序，支持双向排序且未知额度始终在末尾", async ({
  page,
}) => {
  await openFixture(page);
  await expect
    .poll(() => rows(page))
    .toEqual([
      "alpha@example.test",
      "charlie@example.test",
      "echo@example.test",
      "bravo@example.test",
      "delta@example.test",
      "zulu@example.test",
    ]);
  await sort(page, "email", "descend");
  await expect
    .poll(() => rows(page))
    .toEqual([
      "zulu@example.test",
      "echo@example.test",
      "delta@example.test",
      "charlie@example.test",
      "bravo@example.test",
      "alpha@example.test",
    ]);
  await sort(page, "quota", "ascend");
  await expect
    .poll(() => rows(page))
    .toEqual([
      "bravo@example.test",
      "delta@example.test",
      "echo@example.test",
      "charlie@example.test",
      "alpha@example.test",
      "zulu@example.test",
    ]);
  await sort(page, "quota", "descend");
  await expect
    .poll(() => rows(page))
    .toEqual([
      "alpha@example.test",
      "charlie@example.test",
      "echo@example.test",
      "bravo@example.test",
      "delta@example.test",
      "zulu@example.test",
    ]);
  await sort(page, "email", "ascend");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 1,
    ),
  ).toBe(false);
});

test("大厅支持下次重置时间近远排序，未设置按自然顺序放置", async ({ page }) => {
  const fixtures = [
    { ...common, id: "none", email: "none@example.test", reset_at: null },
    {
      ...common,
      id: "late",
      email: "late@example.test",
      reset_at: "2026-02-01T00:00:00Z",
    },
    {
      ...common,
      id: "early",
      email: "early@example.test",
      reset_at: "2026-01-01T00:00:00Z",
    },
  ];
  await openFixture(page, false, fixtures);
  await sort(page, "reset_at", "ascend");
  await expect
    .poll(() => rows(page))
    .toEqual(["none@example.test", "early@example.test", "late@example.test"]);
  await sort(page, "reset_at", "descend");
  await expect
    .poll(() => rows(page))
    .toEqual(["late@example.test", "early@example.test", "none@example.test"]);
});

test("移动卡片独立显示重置时间，隐藏长更新摘要", async ({ page }, testInfo) => {
  const fixtures = [
    {
      ...common,
      id: "summary",
      email: "summary@example.test",
      quota: 0,
      reset_at: "2026-09-22T03:59:00Z",
      recent_updates: [
        {
          id: 1,
          actor: "系统",
          kind: "quota_updated",
          created_at: "2026-09-18T08:25:00Z",
          account_id: "summary",
          target_user_id: null,
          details: {
            quota: 0,
            reset_at: "2026-09-22T03:59:00Z",
            note: "旧额度更新摘要不应挤占手机卡片空间",
          },
        },
      ],
    },
  ];
  await openFixture(page, false, fixtures);
  if (await page.locator(".mobile-nav").isVisible()) {
    const card = page.locator(".mobile-cards .account-card");
    await expect(card.locator(".card-reset")).toHaveText(
      "下次重置：2026-09-22 11:59",
    );
    await expect(card).not.toContainText("旧额度更新摘要");
    await expect(card).not.toContainText("2026-09-18 16:25");
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    ).toBe(true);
  } else {
    const titles = await page.locator(".ant-table-thead th").allTextContents();
    expect(titles.indexOf("下次重置")).toBe(titles.indexOf("剩余额度") + 1);
    await expect(page.locator(".desktop-table")).toContainText(
      "2026-09-18 16:25",
    );
  }
  await page.screenshot({
    path: testInfo.outputPath("quota-reset-layout.png"),
    fullPage: true,
  });
});

test("五种状态默认全选，单选、多选和取消某类互不重复", async ({ page }) => {
  await openFixture(page);
  await page.getByRole("button", { name: "筛选状态", exact: true }).click();
  const panel = page.locator(".status-filter-panel");
  await expect(panel.getByRole("checkbox")).toHaveCount(6);
  await expect(
    panel.getByRole("checkbox", { name: "全选", exact: true }),
  ).toBeChecked();
  await panel
    .getByRole("checkbox", { name: "人数已满", exact: true })
    .uncheck();
  await expect
    .poll(() => rows(page))
    .toEqual([
      "alpha@example.test",
      "charlie@example.test",
      "bravo@example.test",
      "delta@example.test",
      "zulu@example.test",
    ]);
  await panel.getByRole("checkbox", { name: "全选", exact: true }).check();
  await expect(page.locator(".list-caption strong")).toHaveText("6");
  await panel.getByRole("button", { name: "清空", exact: true }).click();
  await expect(page.locator(".list-caption strong")).toHaveText("0");
  await panel.getByRole("checkbox", { name: "异常", exact: true }).check();
  await expect.poll(() => rows(page)).toEqual(["bravo@example.test"]);
  await panel.getByRole("checkbox", { name: "人数已满", exact: true }).check();
  await expect
    .poll(() => rows(page))
    .toEqual(["echo@example.test", "bravo@example.test"]);
  await panel.getByRole("checkbox", { name: "全选", exact: true }).check();
  await expect(page.locator(".list-caption strong")).toHaveText("6");
});

test("领用按钮下方优先提示个人名额已满", async ({ page }) => {
  await openFixture(page, true);
  const container = (await page.locator(".mobile-nav").isVisible())
    ? page.locator(".mobile-cards .account-card")
    : page.locator(".desktop-table .ant-table-row");
  const full = container.filter({ hasText: "echo@example.test" });
  await expect(
    full.getByRole("button", { name: "领用", exact: true }),
  ).toBeDisabled();
  await expect(full.locator(".claim-blocked-reason")).toHaveText(
    "个人名额已满",
  );
  const button = await full
    .getByRole("button", { name: "领用", exact: true })
    .boundingBox();
  const reason = await full.locator(".claim-blocked-reason").boundingBox();
  expect(reason!.y).toBeGreaterThanOrEqual(button!.y + button!.height);
});

test("筛选控件统一高度与外观，领用和已领用按钮等宽", async ({ page }) => {
  const fixtures = accounts.map((a) =>
    a.id === "a"
      ? { ...a, has_open_claim: true, can_claim: false, active_count: 1 }
      : a,
  );
  await openFixture(page, false, fixtures);
  const controls = [
    page.locator(".account-filters > .ant-input-affix-wrapper"),
    page.locator(".account-filters > .ant-select .ant-select-selector").first(),
    page.getByRole("button", { name: "筛选状态", exact: true }),
  ];
  const styles = await Promise.all(
    controls.map((control) =>
      control.evaluate((el) => {
        const style = getComputedStyle(el);
        return {
          height: el.getBoundingClientRect().height,
          radius: style.borderRadius,
          border: style.borderColor,
          background: style.backgroundColor,
        };
      }),
    ),
  );
  expect(styles[0]).toEqual(styles[2]);
  expect(styles[1]).toEqual(styles[2]);
  const claimed = page.getByRole("button", { name: "已领用", exact: true });
  const available = page
    .getByRole("button", { name: "领用", exact: true })
    .first();
  expect((await claimed.boundingBox())!.width).toBe(
    (await available.boundingBox())!.width,
  );
  expect((await claimed.boundingBox())!.width).toBeLessThanOrEqual(100);
});

test("每页默认20个，可修改数量，翻页后搜索与调整数量正确复位", async ({
  page,
}) => {
  const fixtures = Array.from({ length: 47 }, (_, index) => ({
    ...common,
    id: `page-${index}`,
    email: `page-${String(index + 1).padStart(3, "0")}@example.test`,
  }));
  await openFixture(page, false, fixtures);
  await expect(page.locator(".page-summary")).toHaveText(
    "每页 20 个 · 共 3 页",
  );
  await expect.poll(async () => (await rows(page)).length).toBe(20);
  await page.locator(".account-pagination .ant-pagination-next").click();
  await expect
    .poll(async () => (await rows(page))[0])
    .toBe("page-021@example.test");
  await page.locator(".account-pagination .ant-pagination-next").click();
  await expect.poll(async () => (await rows(page)).length).toBe(7);
  await page.locator(".list-count-controls .ant-select-selector").click();
  await page
    .locator(".ant-select-dropdown:visible")
    .getByText("10 个 / 页", { exact: true })
    .click();
  await expect(page.locator(".page-summary")).toHaveText(
    "每页 10 个 · 共 5 页",
  );
  await expect
    .poll(async () => (await rows(page))[0])
    .toBe("page-001@example.test");
  await page.locator(".account-pagination .ant-pagination-next").click();
  await page.getByLabel("搜索账号邮箱", { exact: true }).fill("page-047");
  await expect(page.locator(".page-summary")).toHaveText(
    "每页 10 个 · 共 1 页",
  );
  await expect.poll(() => rows(page)).toEqual(["page-047@example.test"]);
  await page.getByLabel("搜索账号邮箱", { exact: true }).fill("");
  await expect
    .poll(async () => (await rows(page))[0])
    .toBe("page-001@example.test");
  await page.locator(".list-count-controls .ant-select-selector").click();
  await page
    .locator(".ant-select-dropdown:visible")
    .getByText("50 个 / 页", { exact: true })
    .click();
  await expect(page.locator(".page-summary")).toHaveText(
    "每页 50 个 · 共 1 页",
  );
  await expect.poll(async () => (await rows(page)).length).toBe(47);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth + 1,
    ),
  ).toBe(false);
});

test("耗尽状态使用系统判定，等于阈值及未知额度不会误判", async ({ page }) => {
  const fixtures = [4, 5, 9, 10, null].map((quota, index) => ({
    ...common,
    id: `threshold-${index}`,
    email: `threshold-${index}@example.test`,
    quota,
  }));
  await openFixture(page, false, fixtures, 10);
  await page.getByRole("button", { name: "筛选状态", exact: true }).click();
  const panel = page.locator(".status-filter-panel");
  await panel.getByRole("button", { name: "清空", exact: true }).click();
  await expect(
    panel.getByRole("checkbox", { name: "额度已耗尽", exact: true }),
  ).not.toBeChecked();
  await panel.getByText("额度已耗尽", { exact: true }).click();
  await expect
    .poll(() => rows(page))
    .toEqual([
      "threshold-2@example.test",
      "threshold-1@example.test",
      "threshold-0@example.test",
    ]);
});

import { test, expect, type Page, type Locator } from "@playwright/test";

// The serving deployment provides assets only; all business requests are mocked.
async function openPicker(page: Page) {
  const account = {
    id: "time-example",
    email: "time-example@example.test",
    mail_tool: "mailcom",
    mail_tool_name: "mail.com",
    tier: "20x",
    disabled: false,
    created_at: "2026-01-01T00:00:00Z",
    expires_at: "2035-06-17T04:30:00Z",
    capacity: 3,
    effective_capacity: 3,
    quota: 80,
    quota_depleted: false,
    reset_at: null,
    quota_reset_interval_days: null,
    health: "normal",
    health_categories: [],
    health_note: "",
    health_version: 1,
    can_claim: true,
    has_open_claim: false,
    blocked_reasons: [],
    active_count: 0,
    current_users: [],
    recent_updates: [],
    group_ids: [],
    user_ids: [],
  };
  await page.route("**/api/v1/**", async (route) => {
    expect(route.request().method()).toBe("GET");
    const path = new URL(route.request().url()).pathname;
    const data = path.endsWith("/auth/me")
      ? {
          user: {
            id: "time-admin",
            username: "time-admin",
            display_name: "测试管理员",
            role: "admin",
            must_change_password: false,
            effective_claim_limit: 2,
            claims_used: 0,
            group_ids: [],
          },
          csrf_token: "fictional-time-token",
        }
      : path.endsWith("/accounts")
        ? [account]
        : path.endsWith("/mail-tools")
          ? { default: "mailcom", items: [{ id: "mailcom", name: "mail.com" }] }
          : [];
    await route.fulfill({ json: data });
  });
  await page.goto("/");
  await expect(page.locator(".app-shell")).toBeVisible();
  const mobile = page.locator(".mobile-nav");
  await (
    (await mobile.isVisible())
      ? mobile.getByRole("button", { name: "账号管理", exact: true })
      : page
          .locator(".sidebar")
          .getByRole("menuitem", { name: "账号管理", exact: true })
  ).click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const input = page.getByLabel("过期时间（北京时间，留空不过期）");
  await expect(input).toHaveValue("2035-06-17 12:30");
  await input.click();
  const popup = page.getByRole("dialog", {
    name: "选择日期和时间",
    exact: true,
  });
  await expect(popup).toBeVisible();
  return {
    input,
    popup,
    hours: popup.getByRole("listbox", { name: "小时", exact: true }),
    minutes: popup.getByRole("listbox", { name: "分钟", exact: true }),
  };
}

test("日期时间统一确认，取消、关闭和清除不会遗留草稿", async ({ page }) => {
  const { input, popup, hours, minutes } = await openPicker(page);
  await expect(page.locator(".date-time-input input")).toHaveCount(1);
  await popup.locator('td[title="2035-06-18"]').click();
  await hours.getByRole("option", { name: "23", exact: true }).click();
  await expect(popup.locator(".date-time-panel-header strong")).toHaveText(
    "2035-06-18 23:30",
  );
  await expect(input).toHaveValue("2035-06-17 12:30");
  await popup.getByRole("button", { name: "取消", exact: true }).click();
  await expect(popup).toBeHidden();
  await input.click();
  await expect(hours.getByRole("option", { selected: true })).toHaveText("12");
  await expect(minutes.getByRole("option", { selected: true })).toHaveText(
    "30",
  );

  await hours.getByRole("option", { name: "20", exact: true }).click();
  const modal = (await page.locator(".ant-modal-content").boundingBox())!;
  const overlay = (await page
    .locator(".bounded-date-time-popup:visible")
    .boundingBox())!;
  // A taller form can flip the popup over its heading on desktop as well.
  // Click actual form padding outside the picker, without hitting its mask.
  await page.mouse.click(
    (modal.x + overlay.x) / 2,
    overlay.y + overlay.height / 2,
  );
  await expect(popup).toBeHidden();
  await input.click();
  await expect(hours.getByRole("option", { selected: true })).toHaveText("12");
  await hours.focus();
  await page.keyboard.press("Escape");
  await expect(popup).toBeHidden();
  await expect(page.locator(".ant-modal")).toBeVisible();

  await input.press("ArrowDown");
  await expect(hours).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(popup).toBeHidden();
  await expect(input).toHaveValue("2035-06-17 13:30");
  await expect(page.locator(".ant-modal")).toBeVisible();
  await page
    .getByRole("button", { name: "清除日期和时间", exact: true })
    .click();
  await expect(input).toHaveValue("");
  await expect(popup).toBeHidden();
  await input.click();
  await expect(hours.getByRole("option", { selected: true })).toHaveText("00");
  await expect(minutes.getByRole("option", { selected: true })).toHaveText(
    "00",
  );
});

test("滚动时修改日期不回跳，时分点选、键盘及滚轮均有边界", async ({
  page,
}, testInfo) => {
  const { input, popup, hours, minutes } = await openPicker(page);
  await hours.hover();
  await page.mouse.wheel(0, 72);
  await popup.locator('td[title="2035-06-19"]').click();
  await expect(hours.getByRole("option", { selected: true })).toHaveText("14");
  await minutes.hover();
  await page.mouse.wheel(0, 36);
  await expect(minutes.getByRole("option", { selected: true })).toHaveText(
    "31",
  );
  await expect(popup.locator(".date-time-panel-header strong")).toHaveText(
    "2035-06-19 14:31",
  );
  for (const [column, maximum] of [
    [hours, "23"],
    [minutes, "59"],
  ] as const) {
    await column.focus();
    await page.keyboard.press("End");
    await page.keyboard.press("ArrowDown");
    await column.hover();
    await page.mouse.wheel(0, 200);
    await expect(column.getByRole("option", { selected: true })).toHaveText(
      maximum,
    );
    await page.keyboard.press("Home");
    await page.keyboard.press("ArrowUp");
    await page.mouse.wheel(0, -200);
    await expect(column.getByRole("option", { selected: true })).toHaveText(
      "00",
    );
    await column.getByRole("option", { name: maximum, exact: true }).click();
    await expect(column.getByRole("option", { selected: true })).toHaveText(
      maximum,
    );
  }
  await page.screenshot({
    path: testInfo.outputPath("date-time-picker.png"),
    fullPage: true,
  });
  await popup.getByRole("button", { name: "确定", exact: true }).click();
  await expect(input).toHaveValue("2035-06-19 23:59");
});

async function swipe(page: Page, column: Locator, delta: number) {
  await column.scrollIntoViewIfNeeded();
  const box = (await column.boundingBox())!;
  const client = await page.context().newCDPSession(page);
  const x = box.x + box.width / 2,
    y = box.y + box.height / 2;
  try {
    await client.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x, y }],
    });
    for (let step = 1; step <= 8; step++) {
      await client.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [{ x, y: y + (delta * step) / 8 }],
      });
      await page.waitForTimeout(20);
    }
    await client.send("Input.dispatchTouchEvent", {
      type: "touchEnd",
      touchPoints: [],
    });
  } finally {
    await client.detach();
  }
}

test("浏览年月不提交草稿，闰日与时分一起确认", async ({ page }) => {
  const { input, popup } = await openPicker(page);
  const selectors = popup.locator(
    ".ant-picker-calendar-header .ant-select-selector",
  );
  await selectors.nth(0).click();
  await page
    .locator(".ant-select-dropdown:visible")
    .getByText("2036年", { exact: true })
    .click();
  await expect(popup).toBeVisible();
  await selectors.nth(1).click();
  await page
    .locator(".ant-select-dropdown:visible")
    .getByText("2月", { exact: true })
    .click();
  await expect(popup.locator(".date-time-panel-header strong")).toHaveText(
    "2035-06-17 12:30",
  );
  await popup.locator('td[title="2036-02-29"]').click();
  await popup.getByRole("button", { name: "确定", exact: true }).click();
  await expect(input).toHaveValue("2036-02-29 12:30");
});

test("窄屏触摸滑动可选时分且不循环，确认按钮可达", async ({
  page,
  isMobile,
}, testInfo) => {
  test.skip(!isMobile, "Touch interaction uses the mobile browser project");
  await page.setViewportSize({ width: 320, height: 568 });
  const { input, popup, hours, minutes } = await openPicker(page);
  await swipe(page, hours, -54);
  await expect
    .poll(async () =>
      Number(await hours.getByRole("option", { selected: true }).textContent()),
    )
    .toBeGreaterThan(12);
  await hours.getByRole("option", { name: "00", exact: true }).tap();
  await swipe(page, hours, 54);
  await expect(hours.getByRole("option", { selected: true })).toHaveText("00");
  await minutes.getByRole("option", { name: "59", exact: true }).tap();
  await swipe(page, minutes, -54);
  await expect(minutes.getByRole("option", { selected: true })).toHaveText(
    "59",
  );
  const bounds = (await page
    .locator(".bounded-date-time-popup:visible")
    .boundingBox())!;
  expect(bounds.x).toBeGreaterThanOrEqual(0);
  expect(bounds.y).toBeGreaterThanOrEqual(0);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(321);
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(569);
  const confirm = popup.getByRole("button", { name: "确定", exact: true });
  const confirmBounds = (await confirm.boundingBox())!;
  expect(confirmBounds.y + confirmBounds.height).toBeLessThanOrEqual(568);
  await page.screenshot({
    path: testInfo.outputPath("date-time-touch.png"),
    fullPage: true,
  });
  await confirm.tap();
  await expect(input).toHaveValue("2035-06-17 00:59");
});

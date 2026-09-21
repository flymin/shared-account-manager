import {
  test,
  expect,
  request,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { readFileSync } from "node:fs";
const ADMIN_PASSWORD = "Fictional-E2E-Admin-Only!";
const INITIAL = "Fictional-E2E-Initial!";
const CHANGED = "Fictional-E2E-Changed!";
let admin: APIRequestContext;
let csrf = "";
const stamp = () => `${Date.now()}${Math.floor(Math.random() * 10000)}`;
async function adminApi(path: string, method = "GET", data?: any) {
  const r = await admin.fetch("/api/v1" + path, {
    method,
    data,
    headers: { "X-CSRF-Token": csrf },
  });
  expect(r.ok(), await r.text()).toBeTruthy();
  return r.json();
}
async function prepare() {
  admin = await request.newContext({ baseURL: process.env.E2E_BASE_URL });
  let r = await admin.post("/api/v1/auth/login", {
    data: { username: "admin", password: ADMIN_PASSWORD },
    headers: { "X-Login-Request": "1" },
  });
  if (!r.ok()) {
    if (!process.env.E2E_ADMIN_PASSWORD_FILE)
      throw new Error("Set E2E_ADMIN_PASSWORD_FILE for a new test deployment");
    const password = readFileSync(
      process.env.E2E_ADMIN_PASSWORD_FILE,
      "utf8",
    ).trim();
    r = await admin.post("/api/v1/auth/login", {
      data: { username: "admin", password },
      headers: { "X-Login-Request": "1" },
    });
    expect(r.ok()).toBeTruthy();
    csrf = (await r.json()).csrf_token;
    await adminApi("/auth/password", "PUT", {
      current_password: password,
      new_password: ADMIN_PASSWORD,
    });
    r = await admin.post("/api/v1/auth/login", {
      data: { username: "admin", password: ADMIN_PASSWORD },
      headers: { "X-Login-Request": "1" },
    });
  }
  expect(r.ok()).toBeTruthy();
  csrf = (await r.json()).csrf_token;
}
async function loginUI(page: Page, username: string, password: string) {
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录工作空间" }).click();
}
async function nav(page: Page, name: string) {
  await expect(page.locator(".app-shell")).toBeVisible();
  const mobile = await page.locator(".mobile-nav").isVisible();
  await (
    mobile
      ? page.locator(".mobile-nav").getByRole("button", { name, exact: true })
      : page.locator(".sidebar").getByRole("menuitem", { name, exact: true })
  ).click();
}
async function provision() {
  const id = stamp();
  const username = `u${id}`;
  const user = await adminApi("/users", "POST", {
    username,
    display_name: `用户${id}`,
    password: INITIAL,
  });
  const email = `account-${id}@example.test`;
  await adminApi("/account-imports", "POST", {
    tier: "20x",
    text: `${email}----Test-Vendor-Password!----Test-Auth-Password!`,
    user_ids: [user.id],
  });
  const account = (await adminApi("/accounts")).find(
    (a: any) => a.email === email,
  );
  return { user, username, email, account };
}
async function firstLogin(page: Page, username: string) {
  await loginUI(page, username, INITIAL);
  await expect(
    page.getByRole("heading", { name: "设置您的登录密码" }),
  ).toBeVisible();
  await page.getByLabel("当前密码", { exact: true }).fill(INITIAL);
  await page.getByLabel("新密码（至少 15 位）").fill(CHANGED);
  await page.getByRole("button", { name: "更新密码并重新登录" }).click();
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(CHANGED);
  await page.getByRole("button", { name: "登录工作空间" }).click();
  await expect(
    page.getByRole("heading", { name: "账号大厅", exact: true }),
  ).toBeVisible();
}
async function modal(page: Page) {
  return page.locator(".ant-modal").filter({ visible: true });
}
async function claimUI(page: Page, warning = false) {
  await page.getByRole("button", { name: "领用", exact: true }).click();
  if (warning) await page.getByRole("button", { name: "确认并领用" }).click();
  await expect(
    page.getByText("领用成功，请到“我的领用”查看两段密码"),
  ).toBeVisible();
  await nav(page, "我的领用");
  await expect(page.locator(".credentials")).toBeVisible();
}
async function futureInput(page: Page) {
  const dt = new Date(Date.now() + 86400000);
  const text = new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
    .format(dt)
    .replace(" ", "T");
  await page.getByLabel("下次重置时间（北京时间）").click();
  const popup = page.locator(".bounded-date-time-popup:visible");
  await popup.locator(`td[title="${text.split("T")[0]}"]`).click();
  await popup
    .locator('[data-type="hour"]')
    .getByText(text.slice(11, 13), { exact: true })
    .click();
  await popup
    .locator('[data-type="minute"]')
    .getByText(text.slice(14, 16), { exact: true })
    .click();
  await popup.getByRole("button", { name: "确定", exact: true }).click();
}

test.beforeAll(prepare);
test.afterAll(async () => {
  await admin?.dispose();
});

test("首次改密拒绝过短和易猜密码，并允许强口令", async ({ page }) => {
  const item = await provision();
  try {
    await loginUI(page, item.username, INITIAL);
    await page.getByLabel("当前密码", { exact: true }).fill(INITIAL);
    const input = page.getByLabel("新密码（至少 15 位）");
    await input.fill("short-test");
    await page.getByRole("button", { name: "更新密码并重新登录" }).click();
    await expect(
      page.getByText("密码须为 15–72 个字符", { exact: true }),
    ).toBeVisible();
    await input.fill("12345678901234567890");
    await page.getByRole("button", { name: "更新密码并重新登录" }).click();
    await expect(page.getByText(/密码过于容易猜测/)).toBeVisible();
    await input.fill(CHANGED);
    await page.getByRole("button", { name: "更新密码并重新登录" }).click();
    await page.getByLabel("用户名", { exact: true }).fill(item.username);
    await page.getByLabel("密码", { exact: true }).fill(CHANGED);
    await page.getByRole("button", { name: "登录工作空间" }).click();
    await expect(
      page.getByRole("heading", { name: "账号大厅", exact: true }),
    ).toBeVisible();
  } finally {
    await adminApi(`/accounts/${item.account.id}`, "DELETE", {
      reason: "密码策略回归清理",
    });
    await adminApi(`/users/${item.user.id}`, "DELETE");
  }
});

test("管理员维护 账号 2FA，领用者只能取码，取消后停止刷新", async ({
  page,
  browser,
}, testInfo) => {
  const item = await provision();
  await firstLogin(page, item.username);
  await claimUI(page);
  const service = page
    .locator(".verification-section")
    .filter({ has: page.getByText("账号 2FA", { exact: true }) });
  const mailbox = page
    .locator(".verification-section")
    .filter({ has: page.getByText("邮箱 2FA", { exact: true }) });
  await expect(
    service.getByRole("button", { name: "显示 账号 2FA" }),
  ).toHaveCount(0);
  await expect(service.getByText("未设置", { exact: true })).toBeVisible();
  await expect(page.getByLabel("上传账号 2FA二维码")).toHaveCount(0);
  const management = await browser.newPage({ viewport: page.viewportSize() });
  try {
    await loginUI(management, "admin", ADMIN_PASSWORD);
    await nav(management, "账号管理");
    await management.getByPlaceholder("搜索账号邮箱").fill(item.email);
    await management.getByRole("button", { name: "详情", exact: true }).click();
    const configPanel = management
      .locator(".ant-drawer .verification-section")
      .filter({ hasText: "账号 2FA" });
    await management
      .getByLabel("上传账号 2FA二维码")
      .setInputFiles("e2e/totp-fixture.png");
    await expect(
      configPanel.getByRole("button", { name: "更新二维码", exact: true }),
    ).toBeVisible();
    await expect(
      service.getByRole("button", { name: "显示 账号 2FA" }),
    ).toBeVisible();
    await service.getByRole("button", { name: "显示 账号 2FA" }).click();
    await expect(service.locator(".verification-code")).toHaveText(/^\d{6}$/);
    await expect(mailbox).toHaveCount(0);
    await expect(page.getByLabel("上传邮箱 2FA二维码")).toHaveCount(0);
    const response = await page.request.get(
      `/api/v1/accounts/${item.account.id}/verification`,
    );
    expect((await response.json()).two_factor).toMatchObject({
      service: { configured: true, version: 1 },
    });
    await page.screenshot({
      path: testInfo.outputPath("verification.png"),
      fullPage: true,
    });
    await nav(page, "账号大厅");
    await nav(page, "我的领用");
    await expect(page.locator(".verification-code")).toHaveCount(0);
    await expect(
      service.getByRole("button", { name: "显示 账号 2FA" }),
    ).toBeVisible();
    await management
      .getByLabel("上传账号 2FA二维码")
      .setInputFiles("e2e/totp-fixture.png");
    await management.getByRole("button", { name: "替换", exact: true }).click();
    await expect(management.locator(".ant-modal")).toHaveCount(0);
    await expect
      .poll(
        async () =>
          (await adminApi(`/accounts/${item.account.id}/verification`))
            .two_factor.service.version,
      )
      .toBe(2);
    await service.getByRole("button", { name: "显示 账号 2FA" }).click();
    await expect(service.locator(".verification-code")).toHaveText(/^\d{6}$/);
    await configPanel
      .getByRole("button", { name: "取消设置", exact: true })
      .click();
    await management
      .getByRole("dialog", { name: "取消账号 2FA设置？", exact: true })
      .getByRole("button", { name: "取消设置", exact: true })
      .click();
    await expect(
      configPanel.getByText("未设置", { exact: true }),
    ).toBeVisible();
    await expect(service.getByText("未设置", { exact: true })).toBeVisible();
    await expect(service.locator(".verification-result")).toHaveCount(0);
    await expect(
      service.getByRole("button", { name: /二维码|取消设置|刷新/ }),
    ).toHaveCount(0);
    const removed = await adminApi(`/accounts/${item.account.id}/verification`);
    expect(removed.two_factor.service).toMatchObject({
      configured: false,
      version: 3,
    });
  } finally {
    await management.close();
  }
});

test("管理员通过界面创建组、用户并预览导入与编辑账号", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const id = stamp();
  const name = `测试组${id}`,
    username = `ui${id}`,
    email = `ui-${id}@example.test`;
  await loginUI(page, "admin", ADMIN_PASSWORD);
  await nav(page, "用户与用户组");
  await page.getByRole("tab", { name: /用户组/ }).click();
  await page.getByRole("button", { name: "添加用户组", exact: true }).click();
  await page.getByLabel("组名", { exact: true }).fill(name);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".ant-modal")).toHaveCount(0);
  while (!(await page.getByRole("cell", { name, exact: true }).count())) {
    const next = page.locator(
      ".ant-pagination-next:not(.ant-pagination-disabled):visible",
    );
    await expect(next).toBeVisible();
    await next.click();
  }
  await expect(page.getByRole("cell", { name, exact: true })).toBeVisible();
  await page.getByRole("tab", { name: /^用户 ·/ }).click();
  await page.getByRole("button", { name: "添加用户", exact: true }).click();
  await page.getByLabel("登录用户名").fill(username);
  await page.getByLabel("显示姓名").fill(username);
  await page.getByLabel("初始密码（至少 15 位）").fill(INITIAL);
  await page.getByLabel("所属用户组").click();
  await page.getByLabel("所属用户组").fill(name);
  await page
    .locator(".ant-select-dropdown:visible")
    .getByText(name, { exact: true })
    .click();
  await page.getByLabel("显示姓名").click();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".ant-modal")).toHaveCount(0);
  await nav(page, "账号管理");
  await page.getByRole("button", { name: "批量登记账号" }).click();
  await page.getByLabel("额度自动重置间隔（天，留空沿用全局）").fill("3");
  await expect(
    page.locator(".import-panel").getByText("mail.com", { exact: true }),
  ).toBeVisible();
  await page
    .getByLabel("账号内容")
    .fill(`${email}----UI-Test-Password!----UI-Test-Auth!\ninvalid`);
  await page.getByRole("button", { name: "检查并预览" }).click();
  await expect(
    page.getByText("第 2 行：需要三段内容，以 ---- 分隔"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认整批导入" }),
  ).toBeDisabled();
  await page
    .getByLabel("账号内容")
    .fill(`${email}----UI-Test-Password!----UI-Test-Auth!`);
  await page.getByLabel("授权用户组").click();
  await page.getByLabel("授权用户组").fill(name);
  await page
    .locator(".ant-select-dropdown:visible")
    .getByText(name, { exact: true })
    .click();
  await page.getByLabel("账号内容").click();
  await page.getByRole("button", { name: "检查并预览" }).click();
  await expect(page.getByText("1 个账号检查通过")).toBeVisible();
  await page
    .locator(".ant-form-item")
    .filter({ has: page.locator('label[for="mail_backend"]') })
    .locator(".ant-select-selector")
    .click();
  await page
    .locator(".ant-select-dropdown:visible")
    .getByText("不启用自动取码", { exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "确认整批导入" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "检查并预览" }).click();
  await expect(page.locator(".import-preview")).toContainText("不启用自动取码");
  await page.screenshot({
    path: testInfo.outputPath("mail-backend-import.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "确认整批导入" }).click();
  await expect(page.locator(".import-panel")).toHaveCount(0);
  await page.getByLabel("搜索账号邮箱").fill(email);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(
    page.getByLabel("额度自动重置间隔（天，留空沿用全局）"),
  ).toHaveValue("3");
  await page.getByLabel("额度自动重置间隔（天，留空沿用全局）").fill("14");
  await expect(
    page.locator(".ant-modal").getByText("不启用自动取码", { exact: true }),
  ).toBeVisible();
  await page
    .locator(".ant-form-item")
    .filter({ has: page.locator('label[for="mail_backend"]') })
    .locator(".ant-select-selector")
    .click();
  await page
    .locator(".ant-select-dropdown:visible")
    .getByText("mail.com", { exact: true })
    .click();
  await page.getByLabel("同时领用人数（留空沿用全局）").fill("2");
  await page.screenshot({
    path: testInfo.outputPath("mail-backend-edit.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".ant-modal")).toHaveCount(0);
  const a = (await adminApi("/accounts")).find((x: any) => x.email === email);
  expect(a.capacity).toBe(2);
  expect(a.quota_reset_interval_days).toBe(14);
  expect(a.mail_backend).toBe("mailcom");
  expect(a.group_ids).toHaveLength(1);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(
    page.getByLabel("额度自动重置间隔（天，留空沿用全局）"),
  ).toHaveValue("14");
  await page.getByLabel("额度自动重置间隔（天，留空沿用全局）").clear();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".ant-modal")).toHaveCount(0);
  expect(
    (await adminApi(`/accounts/${a.id}`)).quota_reset_interval_days,
  ).toBeNull();
  await page.screenshot({
    path: testInfo.outputPath("admin-accounts.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBeTruthy();
});

test("邮箱后端关闭后领用者不能自动取码，管理员可恢复且选择持久保存", async ({
  page,
}) => {
  const { username, email, account } = await provision();
  await adminApi(`/accounts/${account.id}`, "PATCH", { mail_backend: null });
  await firstLogin(page, username);
  await claimUI(page);
  await expect(
    page.getByText("未启用自动获取邮箱验证码", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "获取邮箱验证码", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel("邮箱后端", { exact: true })).toHaveCount(0);
  await expect(
    page.locator(".verification").getByText("账号 2FA", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Test-Auth-Password!", { exact: true }),
  ).toBeVisible();
  await adminApi(`/accounts/${account.id}`, "PATCH", {
    mail_backend: "mailcom",
  });
  await expect(
    page.getByRole("button", { name: "获取邮箱验证码", exact: true }),
  ).toBeEnabled();
  await page.reload();
  await nav(page, "我的领用");
  await expect(
    page.getByRole("button", { name: "获取邮箱验证码", exact: true }),
  ).toBeEnabled();
  expect(
    (await adminApi("/accounts")).find((a: any) => a.email === email)
      .mail_backend,
  ).toBe("mailcom");
});

test("用户改密、领用、复制、反馈异常、重新领用及正常归还", async ({
  page,
  context,
}, testInfo) => {
  const item = await provision();
  await firstLogin(page, item.username);
  await page.getByLabel("搜索账号邮箱").fill(item.email);
  await claimUI(page);
  await expect(
    page.getByText("Test-Vendor-Password!", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Test-Auth-Password!", { exact: true }),
  ).toBeVisible();
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: "复制账号密码", exact: true }).click();
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe("Test-Vendor-Password!");
  await page.screenshot({
    path: testInfo.outputPath("my-claims.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "更新额度", exact: true }).click();
  await page.getByLabel("剩余额度（%）").fill("37");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".quota strong")).toHaveText("37%");
  await page.getByRole("button", { name: "反馈异常", exact: true }).click();
  await page.getByLabel("异常描述").fill("测试反馈：回复质量下降");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("异常", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "归还账号", exact: true }).click();
  await page.getByText("异常归还", { exact: true }).click();
  await page.getByLabel("异常描述").fill("问题仍存在");
  await page.getByRole("button", { name: "确认归还" }).click();
  await expect(page.getByText("暂无领用中的账号")).toBeVisible();
  await nav(page, "账号大厅");
  await claimUI(page, true);
  await page.getByRole("button", { name: "确认恢复", exact: true }).click();
  await page.getByRole("button", { name: "确定", exact: true }).click();
  await expect(page.getByText("人数已满", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "归还账号", exact: true }).click();
  await expect(page.getByLabel("剩余额度（%）")).toHaveValue("0");
  await futureInput(page);
  await page.getByRole("button", { name: "确认归还" }).click();
  await expect(page.getByText("暂无领用中的账号")).toBeVisible();
  await nav(page, "领用历史");
  await expect(page.getByText("正常归还", { exact: true })).toBeVisible();
  const a = await adminApi(`/accounts/${item.account.id}`);
  expect(a.quota).toBe(0);
  expect(a.health).toBe("normal");
  expect(a.active_count).toBe(0);
});

test("管理员回收后用户看不到凭据且需主动归还释放名额", async ({
  page,
  browser,
}, testInfo) => {
  const item = await provision();
  await firstLogin(page, item.username);
  await claimUI(page);
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    });
    document.execCommand = () => false;
  });
  await page.getByRole("button", { name: "复制邮箱密码", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "请手动复制" })).toBeVisible();
  const manager = await browser.newContext({
    baseURL: process.env.E2E_BASE_URL,
  });
  const managerPage = await manager.newPage();
  await loginUI(managerPage, "admin", ADMIN_PASSWORD);
  await nav(managerPage, "管理概览");
  await managerPage.getByPlaceholder("搜索用户或账号").fill(item.email);
  await managerPage
    .getByRole("row")
    .filter({ hasText: item.email })
    .getByRole("button", { name: "强制回收", exact: true })
    .click();
  await managerPage.getByLabel("回收原因").fill("浏览器回收验收");
  await managerPage.getByRole("button", { name: "确定", exact: true }).click();
  await expect(managerPage.locator(".ant-modal")).toHaveCount(0);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByText("账号已回收", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "请手动复制" })).toHaveCount(0);
  await expect(page.locator(".credentials")).toHaveCount(0);
  await expect(page.locator(".personal-quota strong")).toHaveText("1");
  const a = await adminApi(`/accounts/${item.account.id}`);
  expect(a.active_count).toBe(0);
  await page.screenshot({
    path: testInfo.outputPath("revoked.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "归还并释放名额" }).click();
  await expect(page.locator(".personal-quota strong")).toHaveText("0");
  await manager.close();
});

test("HTTP复制的兼容与手动回退不误报成功", async ({ page }) => {
  const item = await provision();
  await firstLogin(page, item.username);
  await claimUI(page);
  await page.evaluate(() =>
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: undefined,
    }),
  );
  await page.getByRole("button", { name: "复制完整账号" }).click();
  await expect(page.getByText("完整账号已复制")).toBeVisible();
  await page.evaluate(() => {
    document.execCommand = () => false;
  });
  await page.getByRole("button", { name: "复制邮箱密码", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "请手动复制" })).toBeVisible();
  await expect(page.getByLabel("手动复制内容")).toHaveValue(
    "Test-Auth-Password!",
  );
});

test("删除授权用户后仍可编辑账号并追溯已删除对象", async ({ page }) => {
  const item = await provision();
  await adminApi(`/users/${item.user.id}`, "DELETE");
  await loginUI(page, "admin", ADMIN_PASSWORD);
  await nav(page, "账号管理");
  await page.getByLabel("搜索账号邮箱").fill(item.email);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("同时领用人数（留空沿用全局）").fill("3");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".ant-modal")).toHaveCount(0);
  expect((await adminApi(`/accounts/${item.account.id}`)).capacity).toBe(3);
  await adminApi(`/accounts/${item.account.id}`, "DELETE", {
    reason: "审计回归",
  });
  await nav(page, "操作审计");
  await expect(
    page
      .locator(".ant-timeline")
      .getByText(`账号：${item.email}`, { exact: true })
      .first(),
  ).toBeVisible();
  await expect(
    page
      .locator(".ant-timeline")
      .getByText(`用户：${item.user.display_name}（${item.username}）`, {
        exact: true,
      })
      .first(),
  ).toBeVisible();
  const accountFilter = page.locator(".filter-bar .ant-select").first();
  await accountFilter.click();
  await accountFilter.locator("input").fill(item.email);
  await page.getByText(`${item.email}（已删除）`, { exact: true }).click();
  await expect(
    page.locator(".ant-timeline").getByText("删除账号", { exact: true }),
  ).toBeVisible();
});

test("用户组编辑可以添加、移除成员且保留其他组", async ({ page }) => {
  const first = await provision(),
    second = await provision();
  const other = await adminApi("/groups", "POST", {
    name: `other-${stamp()}`,
    user_ids: [first.user.id],
  });
  const name = `000-members-${stamp()}`;
  await loginUI(page, "admin", ADMIN_PASSWORD);
  await nav(page, "用户与用户组");
  await page.getByRole("tab", { name: /用户组/ }).click();
  await page.getByRole("button", { name: "添加用户组", exact: true }).click();
  await page.getByLabel("组名", { exact: true }).fill(name);
  async function selectMember(username: string) {
    await page.getByLabel("组成员", { exact: true }).click();
    await page.getByLabel("组成员", { exact: true }).fill(username);
    await page
      .locator(".ant-select-dropdown:visible .ant-select-item-option")
      .filter({ hasText: username })
      .click();
    await page.getByLabel("组成员", { exact: true }).press("Escape");
  }
  await selectMember(first.username);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".ant-modal")).toHaveCount(0);
  const savedGroups = await adminApi("/groups");
  await expect(
    page.getByRole("tab", {
      name: `用户组 · ${savedGroups.length}`,
      exact: true,
    }),
  ).toBeVisible();
  const targetPage =
    Math.floor(savedGroups.findIndex((g: any) => g.name === name) / 10) + 1;
  const activePage = page.locator(".ant-pagination-item-active:visible");
  while (Number(await activePage.innerText()) !== targetPage) {
    const current = Number(await activePage.innerText());
    await page
      .locator(
        `.ant-pagination-${current < targetPage ? "next" : "prev"}:visible`,
      )
      .click();
    await expect(activePage).toHaveText(
      String(current + (current < targetPage ? 1 : -1)),
    );
  }
  const row = page.getByRole("row").filter({ hasText: name });
  await row.getByRole("button", { name: "编辑", exact: true }).click();
  await page.locator(".ant-modal .ant-select-selection-item-remove").click();
  await selectMember(second.username);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".ant-modal")).toHaveCount(0);
  const group = (await adminApi("/groups")).find((g: any) => g.name === name);
  const users = await adminApi("/users");
  expect(users.find((u: any) => u.id === first.user.id).group_ids).toEqual([
    other.id,
  ]);
  expect(users.find((u: any) => u.id === second.user.id).group_ids).toContain(
    group.id,
  );
});

test("日期时间弹窗可滚动或点选，时分两端不循环", async ({ page }, testInfo) => {
  const item = await provision();
  await adminApi(`/accounts/${item.account.id}`, "PATCH", {
    expires_at: "2035-06-17T04:30:00Z",
  });
  await loginUI(page, "admin", ADMIN_PASSWORD);
  await nav(page, "账号管理");
  await page.getByLabel("搜索账号邮箱").fill(item.email);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const input = page.getByLabel("过期时间（北京时间，留空不过期）");
  await expect(input).toHaveValue("2035-06-17 12:30");
  await input.click();
  const popup = page.locator(".bounded-date-time-popup:visible");
  await popup.locator('td[title="2035-06-18"]').click();
  for (const [type, count, maximum] of [
    ["hour", 24, "23"],
    ["minute", 60, "59"],
  ] as const) {
    const column = popup.locator(`[data-type="${type}"]`);
    const selected = column.getByRole("option", { selected: true });
    await expect(column.getByRole("option")).toHaveCount(count);
    await column.getByText("12", { exact: true }).click();
    await column.hover();
    await page.mouse.wheel(0, 36);
    await expect(selected).toHaveText("13");
    await column.getByText(maximum, { exact: true }).click();
    await column.hover();
    await page.mouse.wheel(0, 100);
    await expect(selected).toHaveText(maximum);
    await column.getByText("00", { exact: true }).click();
    await column.hover();
    await page.mouse.wheel(0, -100);
    await expect(selected).toHaveText("00");
    // Pointer/touch selection is also available without entering text.
    await column.getByText(maximum, { exact: true }).click();
    await expect(selected).toHaveText(maximum);
  }
  await page.screenshot({
    path: testInfo.outputPath("time-picker.png"),
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBeTruthy();
  const bounds = await popup.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(
    page.viewportSize()!.height + 1,
  );
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(
    page.viewportSize()!.width + 1,
  );
  await popup.getByRole("button", { name: "确定", exact: true }).click();
  await expect(input).toHaveValue("2035-06-18 23:59");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.locator(".ant-modal")).toHaveCount(0);
  expect(
    new Date(
      (await adminApi(`/accounts/${item.account.id}`)).expires_at,
    ).toISOString(),
  ).toBe("2035-06-18T15:59:00.000Z");
  await page.getByRole("button", { name: "批量登记账号" }).click();
  await page.getByLabel("过期时间（北京时间，留空不过期）").click();
  await expect(popup.locator('[data-type="hour"]')).toBeVisible();
  await expect(popup.locator('[data-type="minute"]')).toBeVisible();
});

test("停用保留已有领用及密码，大厅隐藏账号并支持恢复启用", async ({
  page,
  browser,
}) => {
  const item = await provision();
  await firstLogin(page, item.username);
  await claimUI(page);
  const manager = await browser.newContext({
    baseURL: process.env.E2E_BASE_URL,
    viewport: page.viewportSize(),
  });
  const managerPage = await manager.newPage();
  try {
    await loginUI(managerPage, "admin", ADMIN_PASSWORD);
    await nav(managerPage, "账号管理");
    await managerPage.getByLabel("搜索账号邮箱").fill(item.email);
    await managerPage
      .getByRole("button", { name: "停用", exact: true })
      .click();
    await managerPage
      .getByRole("button", { name: "确认停用", exact: true })
      .click();
    await expect(
      managerPage.getByRole("button", { name: "恢复启用", exact: true }),
    ).toBeVisible();
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(page.getByText("已停用", { exact: true })).toBeVisible();
    await expect(page.locator(".credentials")).toContainText(
      "Test-Vendor-Password!",
    );
    await expect(page.locator(".personal-quota strong")).toHaveText("1");
    await nav(page, "账号大厅");
    await expect(page.locator(".list-caption strong")).toHaveText("0");
    await nav(page, "我的领用");
    await page.getByRole("button", { name: "更新额度", exact: true }).click();
    await page.getByLabel("剩余额度（%）").fill("62");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.locator(".quota strong")).toHaveText("62%");
    await page.getByRole("button", { name: "归还账号", exact: true }).click();
    await futureInput(page);
    await page.getByRole("button", { name: "确认归还", exact: true }).click();
    await expect(page.locator(".personal-quota strong")).toHaveText("0");
    await managerPage
      .getByRole("button", { name: "恢复启用", exact: true })
      .click();
    await managerPage
      .getByRole("button", { name: "确认恢复启用", exact: true })
      .click();
    await expect(
      managerPage.getByRole("button", { name: "停用", exact: true }),
    ).toBeVisible();
    await nav(page, "账号大厅");
    await expect(
      page.getByRole("button", { name: "领用", exact: true }),
    ).toBeEnabled();
    await claimUI(page);
    const events = await adminApi(`/accounts/${item.account.id}/events`);
    expect(
      events.filter((e: any) => e.kind === "account_disabled"),
    ).toHaveLength(1);
    expect(
      events.filter((e: any) => e.kind === "account_enabled"),
    ).toHaveLength(1);
  } finally {
    await manager.close();
  }
});

test("管理员保存耗尽阈值与重置间隔后刷新仍保留设置", async ({ page }) => {
  const previous = await adminApi("/settings");
  try {
    await loginUI(page, "admin", ADMIN_PASSWORD);
    await nav(page, "系统设置");
    await page.getByLabel("额度耗尽阈值（%）", { exact: true }).fill("8");
    await page
      .getByLabel("默认额度自动重置间隔（天）", { exact: true })
      .fill("10");
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    await expect
      .poll(async () => (await adminApi("/settings")).quota_depleted_threshold)
      .toBe(8);
    expect((await adminApi("/settings")).quota_reset_interval_days).toBe(10);
    await page.reload();
    await nav(page, "系统设置");
    await expect(
      page.getByLabel("额度耗尽阈值（%）", { exact: true }),
    ).toHaveValue("8");
    await expect(
      page.getByLabel("默认额度自动重置间隔（天）", { exact: true }),
    ).toHaveValue("10");
  } finally {
    await adminApi("/settings", "PUT", previous);
  }
});

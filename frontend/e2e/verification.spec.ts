import { test, expect, type Page } from "@playwright/test";

// Every API request is intercepted. These fixtures never access an account,
// mailbox, QR secret, or login session from the deployment serving the assets.
const TEST_TIME = new Date("2026-01-02T12:00:00Z");
const RECEIVED_AT = "2026-01-02T10:12:34Z";
const EMAIL_CODE = "123456";
type Kind = "service";
type Run = {
  id: string;
  status: string;
  deadline: string;
  server_time: string;
  code?: string;
  received_at?: string;
};

function account(id = "account-one") {
  return {
    id,
    email: `${id}@example.test`,
    mail_tool: "mailcom",
    mail_tool_name: "mail.com",
    tier: "20x",
    disabled: false,
    created_at: TEST_TIME.toISOString(),
    expires_at: null,
    capacity: 3,
    effective_capacity: 3,
    quota: 80,
    quota_depleted: false,
    reset_at: null,
    quota_reset_interval_days: null,
    quota_updated_at: TEST_TIME.toISOString(),
    quota_source: null,
    health: "normal",
    health_categories: [],
    health_note: "",
    health_version: 1,
    can_claim: false,
    has_open_claim: true,
    blocked_reasons: [],
    active_count: 1,
    current_users: ["测试领用者"],
    recent_updates: [],
    group_ids: [],
    user_ids: [],
  };
}

async function navigation(page: Page, name: string) {
  await expect(page.locator(".app-shell")).toBeVisible();
  const mobile = page.locator(".mobile-nav");
  await (
    (await mobile.isVisible())
      ? mobile.getByRole("button", { name, exact: true })
      : page.locator(".sidebar").getByRole("menuitem", { name, exact: true })
  ).click();
}

async function fixture(
  page: Page,
  options: { configured?: Kind[]; accounts?: number; admin?: boolean } = {},
) {
  await page.clock.install({ time: TEST_TIME });
  const accounts = [account()];
  if (options.accounts === 2) accounts.push(account("account-two"));
  const requests: { path: string; method: string }[] = [];
  const runs = new Map<string, Run>();
  const jobs = new Map<string, string>();
  let releaseStart: (() => void) | undefined;
  let releaseCredentials: (() => void) | undefined;
  const state = {
    sessionToken: "synthetic-session-one",
    displayName: "测试领用者",
    claimsStatus: 200,
    credentialsStatus: 200,
    delayCredentials: false,
    verificationStatus: 200,
    emailAvailable: true,
    emailConfigVersion: 0,
    emailToolRevision: "synthetic-tool-revision-1",
    mailTools: [{ id: "mailcom", name: "mail.com" }],
    findEmail: false,
    delayStart: false,
    busyAccount: "",
    configs: Object.fromEntries(
      accounts.map((a) => [
        a.id,
        {
          configured: options.configured?.includes("service") || false,
          version: options.configured?.includes("service") ? 1 : 0,
          updated_at: null as string | null,
        },
      ]),
    ),
    releaseStart() {
      if (!releaseStart)
        throw new Error("No delayed start response is pending");
      releaseStart();
      releaseStart = undefined;
    },
    releaseCredentials() {
      if (!releaseCredentials)
        throw new Error("No delayed credential response is pending");
      releaseCredentials();
      releaseCredentials = undefined;
    },
  };
  const user = {
    id: "synthetic-user",
    username: "synthetic-tester",
    display_name: "测试领用者",
    role: options.admin ? "admin" : "user",
    must_change_password: false,
    claim_limit: 3,
    effective_claim_limit: 3,
    claims_used: accounts.length,
    group_ids: [],
  };
  const count = (suffix: string, method = "GET") =>
    requests.filter((r) => r.path.endsWith(suffix) && r.method === method)
      .length;
  const reads = () =>
    requests.filter(
      (r) => r.path.includes("/email-code-runs/") && r.method === "GET",
    ).length;
  const cancellations = () =>
    requests.filter(
      (r) => r.path.includes("/email-code-runs/") && r.method === "DELETE",
    ).length;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    const method = request.method();
    requests.push({ path, method });
    const send = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    const stamp = await page.evaluate(() => Date.now());
    const serverTime = new Date(stamp).toISOString();
    if (path === "/auth/me")
      return send({
        user: {
          ...user,
          id: state.sessionToken,
          display_name: state.displayName,
        },
        csrf_token: state.sessionToken,
      });
    if (path === "/accounts") return send(accounts);
    if (path === "/claims") {
      if (state.claimsStatus !== 200)
        return send({ detail: "测试领用刷新失败" }, state.claimsStatus);
      return send(
        accounts.map((a) => ({
          id: `claim-${a.id}`,
          claimed_at: TEST_TIME.toISOString(),
          returned_at: null,
          invalidated_at: null,
          invalidation_kind: null,
          invalidation_reason: null,
          account: a,
        })),
      );
    }
    if (["/groups", "/users"].includes(path)) return send([]);
    if (path === "/mail-tools")
      return send({
        default: "mailcom",
        items: state.mailTools,
      });
    const accountId = path.split("/")[2];
    const selected = accounts.find((a) => a.id === accountId);
    if (selected && path === `/accounts/${accountId}` && method === "PATCH") {
      Object.assign(selected, request.postDataJSON());
      selected.mail_tool_name =
        state.mailTools.find((tool) => tool.id === selected.mail_tool)?.name ||
        "";
      return send(selected);
    }
    if (selected && path.endsWith("/credentials")) {
      if (state.credentialsStatus !== 200)
        return send({ detail: "测试凭据请求失败" }, state.credentialsStatus);
      if (state.delayCredentials) {
        state.delayCredentials = false;
        await new Promise<void>((resolve) => {
          releaseCredentials = resolve;
        });
      }
      return send({
        email: selected.email,
        password: "Synthetic-Account-Password",
        auth_password: "Synthetic-Mail-Password",
      });
    }
    if (selected && path.endsWith("/verification")) {
      if (state.verificationStatus !== 200)
        return send({ detail: "测试访问已撤回" }, state.verificationStatus);
      const run = runs.get(jobs.get(accountId) || "");
      const owned = run && ["pending", "reading"].includes(run.status);
      return send({
        server_time: serverTime,
        email_available: state.emailAvailable,
        email_config_version: state.emailConfigVersion,
        email_tool_revision: state.emailToolRevision,
        email_unavailable_reason: state.emailAvailable
          ? null
          : "未启用自动获取邮箱验证码",
        two_factor: { service: state.configs[accountId] },
        email:
          state.busyAccount === accountId
            ? {
                owner: "另一位测试领用者",
                mine: false,
                id: null,
                deadline: new Date(TEST_TIME.getTime() + 300000).toISOString(),
              }
            : owned
              ? {
                  owner: user.display_name,
                  mine: true,
                  id: run.id,
                  deadline: run.deadline,
                }
              : null,
      });
    }
    if (selected && path.endsWith("/email-code-runs") && method === "POST") {
      const { id } = request.postDataJSON();
      const run: Run = {
        id,
        status: "pending",
        deadline: new Date(stamp + 300000).toISOString(),
        server_time: serverTime,
      };
      runs.set(id, run);
      jobs.set(accountId, id);
      const response = { ...run };
      if (state.delayStart)
        await new Promise<void>((resolve) => {
          releaseStart = resolve;
        });
      return send(response);
    }
    if (selected && path.includes("/email-code-runs/")) {
      const id = path.split("/").at(-1)!;
      const run = runs.get(id);
      if (!run) return send({ detail: "测试任务不存在" }, 404);
      if (method === "DELETE") {
        run.status = "cancelled";
        delete run.code;
        delete run.received_at;
      } else if (state.findEmail && run.status === "pending") {
        run.status = "found";
        run.code = EMAIL_CODE;
        run.received_at = RECEIVED_AT;
      }
      return send({ ...run, server_time: serverTime });
    }
    if (selected && path.endsWith("/code")) {
      if (!state.configs[accountId].configured)
        return send({ detail: "测试 2FA 尚未配置" }, 404);
      const period = 30;
      const interval = Math.floor(stamp / (period * 1000));
      const response = {
        code: String(200000 + (interval % 100000)),
        valid_until: new Date((interval + 1) * period * 1000).toISOString(),
        server_time: serverTime,
        period,
        version: state.configs[accountId].version,
      };
      return send(response);
    }
    if (
      selected &&
      path.endsWith("/two-factor/service") &&
      ["PUT", "DELETE"].includes(method)
    ) {
      if (!options.admin) return send({ detail: "需要管理员权限" }, 403);
      const config = state.configs[accountId];
      const version = Number(
        new URL(request.url()).searchParams.get("version"),
      );
      if (version !== config.version)
        return send({ detail: "2FA 配置已被更新，请刷新后重试" }, 409);
      config.configured = method === "PUT";
      config.version++;
      config.updated_at = serverTime;
      return send(config);
    }
    if (selected && path.endsWith("/events")) return send([]);
    // Never fall through to a real API, even if the UI gains another request.
    return send(
      { detail: `Unexpected synthetic API request: ${method} ${path}` },
      500,
    );
  });

  async function openClaims() {
    await page.goto("/");
    await navigation(page, "我的领用");
    await expect(page.locator(".verification")).toHaveCount(accounts.length);
    await expect(card().locator(".verification-section")).toHaveCount(2);
  }
  const card = (id = accounts[0].id) =>
    page.locator(".claim-card").filter({ hasText: `${id}@example.test` });
  const getEmail = (id = accounts[0].id) =>
    card(id).getByRole("button", { name: "获取邮箱验证码", exact: true });
  const section = (kind: Kind) =>
    card()
      .locator(".verification-section")
      .filter({
        has: page.locator("strong", {
          hasText: /^账号 2FA$/,
        }),
      });
  const refreshTotp = (kind: Kind) =>
    section(kind).getByRole("button", {
      name: "显示 账号 2FA",
      exact: true,
    });
  return {
    state,
    requests,
    count,
    reads,
    cancellations,
    openClaims,
    card,
    getEmail,
    section,
    refreshTotp,
    hasDelayedStart: () => !!releaseStart,
    hasDelayedCredentials: () => !!releaseCredentials,
  };
}

test.describe("verification with synthetic APIs", () => {
  test.use({ timezoneId: "America/New_York" });

  test("admin selects a newly registered tool by its catalog ID", async ({
    page,
  }) => {
    const f = await fixture(page, { admin: true });
    f.state.mailTools.push({ id: "other_login", name: "Other 登录验证码" });
    await page.goto("/");
    await navigation(page, "账号管理");
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await page
      .locator(".ant-form-item")
      .filter({ has: page.locator('label[for="mail_tool"]') })
      .locator(".ant-select-selector")
      .click();
    await page
      .locator(".ant-select-dropdown:visible")
      .getByText("Other 登录验证码", { exact: true })
      .click();
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.locator(".ant-modal")).toHaveCount(0);
    expect(f.count("/accounts/account-one", "PATCH")).toBe(1);
    await page.getByRole("button", { name: "编辑", exact: true }).click();
    await expect(
      page.locator(".ant-modal").getByText("Other 登录验证码", { exact: true }),
    ).toBeVisible();
  });

  test("disabling mail fetching clears an already displayed code and keeps service 2FA", async ({
    page,
  }) => {
    const f = await fixture(page, { configured: ["service"] });
    f.state.findEmail = true;
    await f.openClaims();
    await f.getEmail().click();
    await page.clock.fastForward(1100);
    await expect(f.card().getByText(EMAIL_CODE, { exact: true })).toBeVisible();
    f.state.emailAvailable = false;
    await page.clock.fastForward(4000);
    await expect(f.getEmail()).toHaveCount(0);
    await expect(f.card().getByText(EMAIL_CODE, { exact: true })).toHaveCount(
      0,
    );
    await expect(
      f.card().getByText("未启用自动获取邮箱验证码", { exact: true }),
    ).toBeVisible();
    await expect(f.refreshTotp("service")).toBeEnabled();
    expect(f.count("/mail-tools")).toBe(0);
    expect(f.cancellations()).toBeGreaterThan(0);
    f.state.emailAvailable = true;
    await page.clock.fastForward(4000);
    await expect(f.getEmail()).toBeEnabled();
  });

  test("shows code and actual local receipt time, stops at the first match, and clears at expiry", async ({
    page,
  }) => {
    const f = await fixture(page);
    f.state.findEmail = true;
    await f.openClaims();
    await f.getEmail().click();
    await page.clock.fastForward(1100);
    await expect(f.card().getByText(EMAIL_CODE, { exact: true })).toBeVisible();
    await expect(f.card().locator("time")).toHaveAttribute(
      "datetime",
      RECEIVED_AT,
    );
    await expect(f.card().locator("time")).toHaveText(
      "接收于 2026-01-02 05:12:34（UTC-05:00）",
    );
    const reads = f.reads();
    await page.clock.fastForward(4000);
    await expect(f.getEmail()).toBeEnabled();
    expect(f.reads()).toBe(reads);
    await page.clock.fastForward(300001);
    await expect(f.card().getByText(EMAIL_CODE, { exact: true })).toHaveCount(
      0,
    );
    await expect(
      f.card().getByText("本轮已结束，可重新获取验证码。", { exact: true }),
    ).toBeVisible();
  });

  for (const changed of ["account", "tool"] as const) {
    test(`${changed} configuration revisions clear old results even when fetching stays available`, async ({
      page,
    }) => {
      const f = await fixture(page);
      f.state.findEmail = true;
      await f.openClaims();
      await f.getEmail().click();
      await page.clock.fastForward(1100);
      await expect(
        f.card().getByText(EMAIL_CODE, { exact: true }),
      ).toBeVisible();
      if (changed === "account") f.state.emailConfigVersion += 1;
      else f.state.emailToolRevision = "synthetic-tool-revision-2";
      await page.clock.fastForward(4000);
      await expect(f.card().getByText(EMAIL_CODE, { exact: true })).toHaveCount(
        0,
      );
      await expect(f.getEmail()).toBeEnabled();
      expect(f.cancellations()).toBeGreaterThan(0);
    });
  }

  test("another holder sees owner and countdown only, while a separate account can start", async ({
    page,
  }) => {
    const f = await fixture(page, { accounts: 2 });
    f.state.busyAccount = "account-one";
    f.state.findEmail = true;
    await f.openClaims();
    await expect(f.getEmail()).toBeDisabled();
    await expect(
      f.card().getByText(/另一位测试领用者 正在获取.*\d+:\d{2}/),
    ).toBeVisible();
    await expect(
      f.card().getByRole("button", { name: /取消.*获取/ }),
    ).toHaveCount(0);
    await expect(f.getEmail("account-two")).toBeEnabled();
    await f.getEmail("account-two").click();
    await page.clock.fastForward(1100);
    await expect(
      f.card("account-two").getByText(EMAIL_CODE, { exact: true }),
    ).toBeVisible();
    await expect(f.card().locator(".verification-code")).toHaveCount(0);
    expect(
      f.requests.filter((r) => r.path.includes("/account-one/email-code-runs")),
    ).toEqual([]);
  });

  test("unconfigured 2FA never fetches a code", async ({ page }) => {
    const f = await fixture(page);
    await f.openClaims();
    await expect(f.card().getByText("未设置", { exact: true })).toHaveCount(1);
    await expect(f.card().locator('input[type="file"]')).toHaveCount(0);
    await expect(
      f.card().getByRole("button", { name: /二维码|取消设置/ }),
    ).toHaveCount(0);
    await expect(
      f.card().getByRole("button", { name: /^显示 .*2FA$/ }),
    ).toHaveCount(0);
    await page.clock.fastForward(60000);
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(f.getEmail()).toBeEnabled();
    expect(f.count("/code")).toBe(0);
  });

  for (const admin of [false, true]) {
    test(`${admin ? "admin" : "user"} can only read configured 2FA in My Claims`, async ({
      page,
    }) => {
      const f = await fixture(page, { admin, configured: ["service"] });
      await f.openClaims();
      await expect(
        f.section("service").getByRole("button", { name: /二维码|取消设置/ }),
      ).toHaveCount(0);
      await expect(f.card().locator('input[type="file"]')).toHaveCount(0);
      await f.refreshTotp("service").click();
      await expect(
        f.section("service").locator(".verification-code"),
      ).toHaveText(/^\d{6}$/);
      f.state.configs["account-one"] = {
        configured: false,
        version: 2,
        updated_at: TEST_TIME.toISOString(),
      };
      await page.clock.fastForward(3100);
      await expect(
        f.section("service").getByText("未设置", { exact: true }),
      ).toBeVisible();
      await expect(
        f.section("service").locator(".verification-result"),
      ).toHaveCount(0);
      const reads = f.count("/code");
      await page.clock.fastForward(60000);
      expect(f.count("/code")).toBe(reads);
    });
  }

  test("only admin account management can enable, replace and cancel 2FA", async ({
    page,
  }) => {
    const f = await fixture(page, { admin: true });
    await f.openClaims();
    await expect(f.card().locator('input[type="file"]')).toHaveCount(0);
    await navigation(page, "账号大厅");
    await page.getByRole("button", { name: "记录", exact: true }).click();
    await expect(page.locator(".ant-drawer .verification")).toBeVisible();
    await expect(page.locator('.ant-drawer input[type="file"]')).toHaveCount(0);
    await page.locator(".ant-drawer-close").click();
    await navigation(page, "账号管理");
    await page.getByRole("button", { name: "详情", exact: true }).click();
    const panel = page
      .locator(".ant-drawer .verification-section")
      .filter({ hasText: "账号 2FA" });
    await expect(
      panel.getByRole("button", { name: "上传二维码", exact: true }),
    ).toBeVisible();
    await panel
      .getByLabel("上传账号 2FA二维码")
      .setInputFiles("e2e/totp-fixture.png");
    await expect(
      panel.getByRole("button", { name: /更新二维码/ }),
    ).toBeVisible();
    expect(f.state.configs["account-one"].version).toBe(1);
    await panel
      .getByLabel("上传账号 2FA二维码")
      .setInputFiles("e2e/totp-fixture.png");
    await page.getByRole("button", { name: "替换", exact: true }).click();
    await expect(page.locator(".ant-modal")).toHaveCount(0);
    expect(f.state.configs["account-one"].version).toBe(2);
    await panel.getByRole("button", { name: "显示 账号 2FA" }).click();
    await expect(panel.locator(".verification-code")).toHaveText(/^\d{6}$/);
    await panel.getByRole("button", { name: "取消设置", exact: true }).click();
    await page.getByRole("button", { name: "保留设置", exact: true }).click();
    expect(f.count("/two-factor/service", "DELETE")).toBe(0);
    await panel.getByRole("button", { name: "取消设置", exact: true }).click();
    await page
      .getByRole("dialog", { name: "取消账号 2FA设置？", exact: true })
      .getByRole("button", { name: "取消设置", exact: true })
      .click();
    await expect(panel.getByText("未设置", { exact: true })).toBeVisible();
    await expect(panel.locator(".verification-result")).toHaveCount(0);
    expect(f.state.configs["account-one"].version).toBe(3);
    await panel
      .getByLabel("上传账号 2FA二维码")
      .setInputFiles("e2e/totp-fixture.png");
    await expect(
      panel.getByRole("button", { name: /更新二维码/ }),
    ).toBeVisible();
    expect(f.state.configs["account-one"].version).toBe(4);
    expect(f.count("/two-factor/service", "PUT")).toBe(3);
    expect(f.count("/two-factor/service", "DELETE")).toBe(1);
  });

  test("a late status response cannot restore a cancelled configuration", async ({
    page,
  }) => {
    await fixture(page, { admin: true, configured: ["service"] });
    await page.goto("/");
    await navigation(page, "账号管理");
    await page.getByRole("button", { name: "详情", exact: true }).click();
    const panel = page
      .locator(".ant-drawer .verification-section")
      .filter({ hasText: "账号 2FA" });
    await expect(
      panel.getByRole("button", { name: "取消设置", exact: true }),
    ).toBeVisible();
    let release: (() => void) | undefined;
    await page.route(
      "**/accounts/account-one/verification",
      async (route) => {
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        await route.fulfill({
          json: {
            server_time: TEST_TIME.toISOString(),
            email_available: true,
            email_config_version: 0,
            email_unavailable_reason: null,
            email: null,
            two_factor: {
              service: { configured: true, version: 1, updated_at: null },
            },
          },
        });
      },
      { times: 1 },
    );
    await page.clock.fastForward(3100);
    await expect.poll(() => !!release).toBe(true);
    await panel.getByRole("button", { name: "取消设置", exact: true }).click();
    await page
      .getByRole("dialog", { name: "取消账号 2FA设置？", exact: true })
      .getByRole("button", { name: "取消设置", exact: true })
      .click();
    await expect(panel.getByText("未设置", { exact: true })).toBeVisible();
    const received = page.waitForResponse(
      "**/accounts/account-one/verification",
    );
    release!();
    await received;
    await page.clock.fastForward(1000);
    await expect(panel.getByText("未设置", { exact: true })).toBeVisible();
    await expect(
      panel.getByRole("button", { name: "显示 账号 2FA" }),
    ).toHaveCount(0);
  });

  test("Service TOTP refreshes for five minutes and mailbox 2FA stays disabled", async ({
    page,
  }) => {
    const f = await fixture(page, { configured: ["service"] });
    await f.openClaims();
    expect(f.count("/code")).toBe(0);
    await f.refreshTotp("service").click();
    await expect(
      f.section("service").locator(".verification-code"),
    ).toBeVisible();
    const chatStarted = await page.evaluate(() => Date.now());
    const chatCode = await f
      .section("service")
      .locator(".verification-code")
      .innerText();
    await expect(f.card().getByText("邮箱 2FA", { exact: true })).toHaveCount(
      0,
    );
    await expect(f.card().getByLabel("上传邮箱 2FA二维码")).toHaveCount(0);
    await page.clock.fastForward(30500);
    await expect(
      f.section("service").locator(".verification-code"),
    ).not.toHaveText(chatCode);
    await page.clock.fastForward(
      chatStarted + 300500 - (await page.evaluate(() => Date.now())),
    );
    await expect(
      f.section("service").locator(".verification-code"),
    ).toHaveCount(0);
    await expect(f.card().locator(".verification-code")).toHaveCount(0);
    await expect(f.card().getByText(/自动更新剩余/)).toHaveCount(0);
    const requests = f.count("/code");
    await page.clock.fastForward(60000);
    await expect(f.getEmail()).toBeEnabled();
    expect(f.count("/code")).toBe(requests);
    expect(
      f.requests.filter((r) => r.path.includes("/two-factor/mail")),
    ).toHaveLength(0);
  });

  test("tab and app focus changes keep the email and TOTP sessions running", async ({
    page,
  }) => {
    const f = await fixture(page, { configured: ["service"] });
    await f.openClaims();
    await f.getEmail().click();
    await expect(
      f.card().getByRole("button", { name: "取消获取", exact: true }),
    ).toBeVisible();
    await f.refreshTotp("service").click();
    await expect(
      f.section("service").locator(".verification-code"),
    ).toBeVisible();
    const code = await f
      .section("service")
      .locator(".verification-code")
      .innerText();
    const otherTab = await page.context().newPage();
    await otherTab.goto("about:blank");
    await otherTab.bringToFront();
    await page.evaluate(() => {
      window.dispatchEvent(new Event("blur"));
      document.dispatchEvent(new Event("visibilitychange"));
    });
    const reads = f.reads();
    await page.clock.fastForward(35000);
    await expect.poll(f.reads).toBeGreaterThan(reads);
    await page.bringToFront();
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(
      f.section("service").locator(".verification-code"),
    ).toBeVisible();
    await expect(
      f.section("service").locator(".verification-code"),
    ).not.toHaveText(code);
    await expect(
      f.card().getByRole("button", { name: "取消获取", exact: true }),
    ).toBeVisible();
    expect(f.cancellations()).toBe(0);
    await otherTab.close();
  });

  for (const destination of ["page", "drawer"] as const) {
    test(`leaving the ${destination} cancels before and after a delayed start response`, async ({
      page,
    }) => {
      const f = await fixture(page, { admin: destination === "drawer" });
      f.state.delayStart = true;
      if (destination === "page") {
        await f.openClaims();
        await f.getEmail().click();
      } else {
        await page.goto("/");
        await page.getByRole("button", { name: "记录", exact: true }).click();
        await page
          .getByRole("button", { name: "获取邮箱验证码", exact: true })
          .click();
      }
      await expect.poll(f.hasDelayedStart).toBe(true);
      if (destination === "page") await navigation(page, "账号大厅");
      else await page.locator(".ant-drawer-close").click();
      await expect.poll(f.cancellations).toBe(1);
      f.state.releaseStart();
      await expect.poll(f.cancellations).toBe(2);
      expect(f.reads()).toBe(0);
      await expect(page.locator(".verification-code")).toHaveCount(0);
    });
  }

  for (const delayed of [false, true]) {
    test(`BFCache lifecycle clears ${delayed ? "starting" : "active"} state and permits a fresh request`, async ({
      page,
    }) => {
      const f = await fixture(page, { configured: ["service"] });
      f.state.delayStart = delayed;
      await f.openClaims();
      await f.refreshTotp("service").click();
      await expect(
        f.section("service").locator(".verification-code"),
      ).toBeVisible();
      await f.getEmail().click();
      if (delayed) await expect.poll(f.hasDelayedStart).toBe(true);
      else
        await expect(
          f.card().getByRole("button", { name: "取消获取", exact: true }),
        ).toBeVisible();
      // Synthetic persisted events avoid browser-specific BFCache eligibility
      // while exercising the same lifecycle boundary with real React state.
      await page.evaluate(() =>
        window.dispatchEvent(
          new PageTransitionEvent("pagehide", { persisted: true }),
        ),
      );
      await expect.poll(f.cancellations).toBe(1);
      await page.evaluate(() =>
        window.dispatchEvent(
          new PageTransitionEvent("pageshow", { persisted: true }),
        ),
      );
      await expect(f.getEmail()).toBeEnabled();
      await expect(f.card().locator(".verification-code")).toHaveCount(0);
      await expect(
        f.card().getByRole("button", { name: "取消获取", exact: true }),
      ).toHaveCount(0);
      if (delayed) {
        f.state.releaseStart();
        await expect.poll(f.cancellations).toBe(2);
        await expect(f.getEmail()).toBeEnabled();
      }
      f.state.delayStart = false;
      await f.getEmail().click();
      await expect(
        f.card().getByRole("button", { name: "取消获取", exact: true }),
      ).toBeVisible();
    });
  }

  test("permission denial prevents a delayed start response from restoring a run", async ({
    page,
  }) => {
    const f = await fixture(page);
    f.state.delayStart = true;
    await f.openClaims();
    await f.getEmail().click();
    await expect.poll(f.hasDelayedStart).toBe(true);
    f.state.verificationStatus = 403;
    await page.clock.fastForward(4000);
    await expect(
      f.card().getByText("测试访问已撤回", { exact: true }),
    ).toBeVisible();
    f.state.releaseStart();
    await expect.poll(f.cancellations).toBe(1);
    await expect(f.getEmail()).toBeDisabled();
    await expect(
      f.card().getByRole("button", { name: "取消获取", exact: true }),
    ).toHaveCount(0);
    expect(f.reads()).toBe(0);
  });

  test("a changed session from auth/me clears both secrets without retaining stale controls", async ({
    page,
  }) => {
    const f = await fixture(page, { configured: ["service"] });
    f.state.findEmail = true;
    await f.openClaims();
    await f.getEmail().click();
    await page.clock.fastForward(1100);
    await expect(f.card().getByText(EMAIL_CODE, { exact: true })).toBeVisible();
    await f.refreshTotp("service").click();
    await expect(f.card().locator(".verification-code")).toHaveCount(2);
    const totpRequests = f.count("/code");
    f.state.sessionToken = "synthetic-session-two";
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(f.card().locator(".verification-code")).toHaveCount(0);
    await expect(f.getEmail()).toBeEnabled();
    expect(f.count("/code")).toBe(totpRequests);
    await f.refreshTotp("service").click();
    await expect(
      f.section("service").locator(".verification-code"),
    ).toBeVisible();
  });

  for (const delayed of [false, true]) {
    test(`session changes clear account passwords even with ${delayed ? "a late old response" : "transient failures"}`, async ({
      page,
    }) => {
      const f = await fixture(page);
      await f.openClaims();
      await expect(
        page.getByText("Synthetic-Account-Password", { exact: true }),
      ).toBeVisible();
      await expect(
        page.getByText("Synthetic-Mail-Password", { exact: true }),
      ).toBeVisible();
      if (delayed) {
        f.state.delayCredentials = true;
        await page.evaluate(() => window.dispatchEvent(new Event("focus")));
        await expect.poll(f.hasDelayedCredentials).toBe(true);
      }
      f.state.sessionToken = "synthetic-session-two";
      f.state.displayName = "另一位测试用户";
      f.state.claimsStatus = 503;
      f.state.credentialsStatus = 503;
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
      await expect(page.locator(".user-name")).toHaveText("另一位测试用户");
      await expect(
        page.getByText("测试领用刷新失败", { exact: true }),
      ).toBeVisible();
      if (delayed) f.state.releaseCredentials();
      await page.clock.fastForward(16000);
      await expect(
        page.getByText("Synthetic-Account-Password", { exact: true }),
      ).toHaveCount(0);
      await expect(
        page.getByText("Synthetic-Mail-Password", { exact: true }),
      ).toHaveCount(0);
      await expect(page.locator(".credentials")).toHaveCount(0);
      await expect(page.locator(".claim-card")).toHaveCount(0);
    });
  }

  test("transient credential errors preserve a running job, but access denial cancels it", async ({
    page,
  }) => {
    const f = await fixture(page);
    await f.openClaims();
    await f.getEmail().click();
    await expect(
      f.card().getByRole("button", { name: "取消获取", exact: true }),
    ).toBeVisible();
    f.state.credentialsStatus = 503;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(
      f.card().getByText("凭据刷新暂时失败", { exact: true }),
    ).toBeVisible();
    await expect(
      f.card().getByRole("button", { name: "取消获取", exact: true }),
    ).toBeVisible();
    expect(f.cancellations()).toBe(0);
    f.state.credentialsStatus = 403;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(
      f.card().getByText("凭据暂不可用", { exact: true }),
    ).toBeVisible();
    await expect.poll(f.cancellations).toBe(1);
    await expect(f.card().locator(".verification")).toHaveCount(0);
  });
});

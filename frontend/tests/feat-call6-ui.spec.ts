// CALL-6 acceptance: FEAT-01 EDITOR 保存版本 / FEAT-02 回收站恢复越界 / FEAT-04 重试后轮询新 job id.
// Authored by the 测试与部署 role; additive only, no business code touched.
import {
  test,
  expect,
  type APIRequestContext,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";

const password = "Browser-password-123";

async function api(
  request: APIRequestContext,
  path: string,
  method = "GET",
  data?: unknown,
) {
  const csrf = (await request.storageState()).cookies.find(
    (c) => c.name === "scriptwriting_csrf",
  )?.value;
  const result = await request.fetch(`/api/v1${path}`, {
    method,
    data,
    headers: csrf ? { "X-CSRF-Token": csrf } : {},
  });
  expect(result.ok(), await result.text()).toBeTruthy();
  return result.status() === 204 ? undefined : result.json();
}

async function setup(page: Page, suffix: string) {
  const request = page.request;
  const boot = await request.post("/api/v1/admin/bootstrap", {
    data: { username: "browseradmin", password },
  });
  await api(request, "/auth/login", "POST", {
    username: "browseradmin",
    password: boot.status() === 201 ? password : password + "-changed",
  });
  if (boot.status() === 201)
    await api(request, "/auth/change-password", "POST", {
      current_password: password,
      new_password: password + "-changed",
    });
  await request.post("http://127.0.0.1:8016/reset");
  const work = await api(request, "/works", "POST", {
    title: "验收作品-" + suffix,
  });
  const draft = {
    episodes: [1].map((n) => ({
      number: n,
      title: `集${n}`,
      scenes: [
        {
          number: 1,
          heading: "夜",
          fragments: [{ id: `f${n}`, blocks: [{ type: "ACTION", text: `独特正文${n}` }] }],
        },
      ],
    })),
  };
  await api(request, `/works/${work.id}/draft`, "PATCH", { content: draft });
  for (const [stage, content] of [
    ["brief", { theme: "勇气" }],
    ["characters", [{ name: "主角" }]],
    ["outline", { premise: "寻找真相" }],
  ] as const) {
    await api(request, `/works/${work.id}/creative/${stage}`, "PATCH", content);
    await api(request, `/works/${work.id}/creative/${stage}/confirm`, "POST");
  }
  await page.goto("/");
  return { work, request };
}

async function addEditor(request: APIRequestContext, workId: string, username: string) {
  const user = await api(request, "/admin/users", "POST", {
    username,
    password,
    role: "WRITER",
  });
  await api(request, `/works/${workId}/members/invitations`, "POST", {
    user_id: user.id,
    role: "EDITOR",
  });
  return user;
}

async function editorSession(
  browser: Browser,
  baseURL: string,
  username: string,
): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext({ baseURL });
  const page = await context.newPage();
  await api(page.request, "/auth/login", "POST", { username, password });
  await api(page.request, "/auth/change-password", "POST", {
    current_password: password,
    new_password: password + "-changed",
  });
  return { context, page };
}

test("FEAT-01 EDITOR 可保存整部版本，恢复仍仅限所有者", async ({ page, browser }) => {
  const { work, request } = await setup(page, "editor-version");
  await addEditor(request, work.id, "feat01-editor");

  const { context, page: editor } = await editorSession(
    browser,
    new URL(page.url()).origin,
    "feat01-editor",
  );
  await api(editor.request, `/works/${work.id}/members/accept`, "POST");
  await editor.goto(`/#work/${work.id}`);
  await expect(
    editor.getByRole("heading", { name: work.title, exact: true }),
  ).toBeVisible();

  const versionsBefore = (await api(editor.request, `/works/${work.id}/versions`))
    .length;

  await editor.getByRole("button", { name: "版本", exact: true }).click();
  const saveButton = editor.getByRole("button", { name: "保存当前版本" });
  await expect(saveButton).toBeVisible();
  // Restoring a version stays owner-only.
  await expect(
    editor.getByRole("button", { name: "恢复此版本" }),
  ).toHaveCount(0);
  await saveButton.click();
  await expect(editor.locator(".toast")).toContainText("已保存整部作品版本。");
  await expect
    .poll(
      async () => (await api(editor.request, `/works/${work.id}/versions`)).length,
      { timeout: 10000 },
    )
    .toBe(versionsBefore + 1);

  // Negative case: EDITOR restoring a version is refused by the server.
  const versions = await api(editor.request, `/works/${work.id}/versions`);
  const csrf = (await editor.request.storageState()).cookies.find(
    (c) => c.name === "scriptwriting_csrf",
  )?.value;
  const refused = await editor.request.fetch(
    `/api/v1/works/${work.id}/versions/${versions[0].id}/restore?expected_revision=1`,
    { method: "POST", headers: csrf ? { "X-CSRF-Token": csrf } : {} },
  );
  expect(refused.status(), await refused.text()).toBe(403);
  expect((await refused.json()).detail.code).toBe("WORK_ACCESS_DENIED");
  await context.close();
});

test("FEAT-02 回收站恢复越界（410）在界面上的文案", async ({ page }) => {
  const { work, request } = await setup(page, "expired-restore");
  await api(request, `/works/${work.id}/trash`, "POST");

  // The backend rejects a restore past the retention deadline with
  // 410 TRASH_RETENTION_EXPIRED (app/main.py:1351, boundary proven by
  // tests/test_feat_call6.py:106). The retention floor is 1 day, so the
  // expired state is reproduced here with the server's exact payload.
  await page.route(`**/api/v1/works/${work.id}/restore`, (route) =>
    route.fulfill({
      status: 410,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          code: "TRASH_RETENTION_EXPIRED",
          message: "the work can no longer be restored",
        },
      }),
    }),
  );

  await page.goto("/");
  await page.getByRole("button", { name: "回收站", exact: true }).click();
  await page
    .getByRole("button", { name: "恢复验收作品-expired-restore", exact: true })
    .click();
  await expect(page.getByRole("alert")).toBeVisible({ timeout: 10000 });
  const shown = await page.getByRole("alert").innerText();
  // The refusal is surfaced to the operator. KNOWN GAP (pre-existing on base
  // 62200d0, not introduced by CALL-6): TRASH_RETENTION_EXPIRED has no entry in
  // errorMessages, so the raw English server string is what the user reads.
  expect(shown).toContain("restored");
  await test.info().attach("过期恢复提示文案", { body: shown, contentType: "text/plain" });
});

test("FEAT-04 重试任务新建 job 并轮询新 id，原失败任务保留血缘", async ({ page }) => {
  const { work, request } = await setup(page, "retry-lineage");
  // An unreachable upstream fails as TRANSIENT_PROVIDER_ERROR, which the
  // server treats as retryable (AUTHENTICATION_ERROR et al. are not).
  const failing = await api(request, "/admin/model-configs", "POST", {
    display_name: "必然失败-retry-lineage",
    model_name: "dead-port",
    base_url: "http://127.0.0.1:1/v1",
    api_key: "browser-fail",
  });
  const first = await api(
    request,
    `/works/${work.id}/generation-jobs`,
    "POST",
    {
      model_config_version_id: failing.id,
      target_type: "EPISODE",
      target_path: { episode: 1 },
      idempotency_key: "feat04-first",
      auto_run: true,
    },
  );
  await expect
    .poll(
      async () =>
        (await api(request, `/works/${work.id}/generation-jobs/${first.id}`)).status,
      { timeout: 20000 },
    )
    .toBe("FAILED");

  await page.goto(`/#work/${work.id}`);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: work.title, exact: true }),
  ).toBeVisible();

  const polled: string[] = [];
  page.on("request", (req) => {
    const m = req.url().match(/\/api\/v1\/works\/[^/]+\/generation-jobs\/([^/?]+)/);
    if (m) polled.push(`${req.method()} ${m[1]}`);
  });

  await page.getByRole("button", { name: "生成任务" }).click();
  await page.getByRole("button", { name: "重试任务" }).click();

  await expect
    .poll(() => polled.filter((entry) => entry.startsWith("POST")).length, {
      timeout: 15000,
    })
    .toBeGreaterThan(0);

  const jobs = await api(request, `/works/${work.id}/generation-jobs`);
  const retried = jobs.find((j: { id: string }) => j.id !== first.id);
  expect(retried, `expected a second job, saw ${JSON.stringify(jobs)}`).toBeTruthy();
  // The UI must poll the NEW job id, not the original failed one.
  await expect
    .poll(() => polled.filter((entry) => entry === `GET ${retried.id}`).length, {
      timeout: 15000,
    })
    .toBeGreaterThan(0);
  // Lineage: the retry points back at the failed job, and the FAILED record is immutable.
  expect(retried.retry_of_job_id).toBe(first.id);
  const original = await api(request, `/works/${work.id}/generation-jobs/${first.id}`);
  expect(original.status).toBe("FAILED");
  expect(original.id).toBe(first.id);
});

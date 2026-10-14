import {
  test,
  expect,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import { readFile } from "node:fs/promises";
const screenshotDirectory = process.env.MUJIAN_SCREENSHOTS;
async function screenshot(page: Page, name: string) {
  if (screenshotDirectory) {
    await page.locator(".toast").waitFor({ state: "hidden" });
    await page.screenshot({
      path: `${screenshotDirectory}/${name}.png`,
      fullPage: true,
    });
  }
}
async function api(
  request: APIRequestContext,
  path: string,
  method = "GET",
  data?: unknown,
) {
  const cookies = (await request.storageState()).cookies;
  const csrf = cookies.find((c) => c.name === "scriptwriting_csrf");
  const response = await request.fetch(`/api/v1${path}`, {
    method,
    data,
    headers: csrf ? { "X-CSRF-Token": csrf.value } : {},
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.status() === 204 ? undefined : response.json();
}
async function login(page: Page, username: string, initial = false) {
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill("Browser-password-123");
  await page.getByRole("button", { name: "进入创作空间" }).click();
  if (initial) {
    await page
      .getByLabel("初始密码", { exact: true })
      .fill("Browser-password-123");
    await page
      .getByLabel("新密码", { exact: true })
      .fill("Browser-password-123-changed");
    await page
      .getByLabel("再次输入新密码")
      .fill("Browser-password-123-changed");
    await page.getByRole("button", { name: "修改密码并进入" }).click();
  }
  await expect(
    page.getByRole("heading", { name: "今天，又有什么故事想说？" }),
  ).toBeVisible({ timeout: 15000 });
}

test("专注写作：草稿、版本、正式稿与片段重写", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(err.message));
  await page.goto("/?demo=1#work/demo-0");
  await expect(
    page.getByRole("heading", { name: "雨夜来信", exact: true }),
  ).toBeVisible();
  await screenshot(page, "writing-desktop");
  const block = page
    .getByLabel("片段 1 剧本块 2 内容", { exact: true })
    .first();
  await block.fill("沈念停在门外，掌心里的信被雨浸透。");
  await page.keyboard.press("Control+s");
  await expect(
    page.getByRole("button", { name: "保存草稿", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "版本", exact: true }).click();
  await expect(page.getByLabel("查看版本").locator("option")).toHaveCount(2);
  await page.getByRole("button", { name: "保存当前版本", exact: true }).click();
  await expect(page.getByLabel("查看版本").locator("option")).toHaveCount(3);
  await page.getByLabel("与另一个版本比较").selectOption("version-0-1");
  await expect(
    page.getByText("两个版本的正文有变化，可并排查看。"),
  ).toBeVisible();
  await page.getByRole("button", { name: "回到写作", exact: true }).click();
  await page.getByRole("button", { name: "发布与导出" }).click();
  await page.getByRole("button", { name: "发布选中版本" }).click();
  await expect(
    page
      .getByRole("combobox", { name: "正式稿", exact: true })
      .locator("option"),
  ).toHaveCount(2);
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Markdown", exact: true }).click();
  const download = await downloaded;
  expect(await readFile((await download.path())!, "utf8")).toContain(
    "沈念停在门外",
  );
  const docxDownloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "DOCX", exact: true }).click();
  const docx = await docxDownloaded;
  expect((await readFile((await docx.path())!)).subarray(0, 2).toString()).toBe(
    "PK",
  );
  await page.getByRole("button", { name: "回到写作", exact: true }).click();
  await block.fill("这句话只属于草稿，不应出现在正式稿。");
  await page.getByRole("button", { name: "发布与导出" }).click();
  const frozen = page.waitForEvent("download");
  await page.getByRole("button", { name: "Markdown", exact: true }).click();
  expect(await readFile((await (await frozen).path())!, "utf8")).not.toContain(
    "这句话只属于草稿",
  );
  await page.getByRole("button", { name: "回到写作", exact: true }).click();
  await block.click();
  await page.getByRole("button", { name: "重写片段", exact: true }).click();
  await page.getByLabel("创作方向").fill("让冲突更有张力");
  await page.getByRole("button", { name: "重写选中片段" }).click();
  await expect(
    page.getByRole("dialog", { name: "确认本次创作的发送范围" }),
  ).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(block).toHaveValue(/铜铃响了/);
  const storage = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("mujian-demo-v2")!),
  );
  expect(storage[0].versions).toHaveLength(3);
  expect(storage[0].draft.episodes[0].number).toBe(1);
  expect(errors).toEqual([]);
});

test("未保存保护、刷新恢复、设定变更审批、深色与手机", async ({ page }) => {
  await page.goto("/?demo=1#work/demo-0");
  const title = page.getByLabel("本集标题");
  await title.fill("未保存的标题");
  await page.getByRole("button", { name: "返回我的作品" }).click();
  await expect(
    page.getByRole("dialog", { name: "还有一些文字没有保存" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "继续编辑", exact: true }).click();
  await page.reload();
  await expect(title).toHaveValue("未保存的标题");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await page.getByRole("button", { name: "查看设定", exact: true }).click();
  await page.getByRole("button", { name: "提出设定变更" }).click();
  await page.getByLabel("核心主题").fill("关于失去和找回的故事");
  await page.getByRole("button", { name: "提交变更提案" }).click();
  await expect(
    page.getByRole("heading", { name: "待审批的变更提案" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "接受变更", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "待审批的变更提案" }),
  ).toHaveCount(0);
  await expect(
    page.getByText("关于失去和找回的故事", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "回到写作", exact: true }).click();
  await page.getByRole("button", { name: "切换深色主题" }).click();
  await screenshot(page, "writing-dark");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "协作", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "版本", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "发布与导出", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await screenshot(page, "writing-mobile");
  await page.getByRole("button", { name: "故事目录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "故事目录", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "01 第一封来信" }).click();
  await expect(page.getByLabel("本集标题")).toHaveValue("第一封来信");
  await page.getByRole("button", { name: "创作对话", exact: true }).click();
  await page.getByLabel("创作对话内容").fill("探索人物的动机");
  await page.getByRole("button", { name: "发送创作对话" }).click();
  await expect(
    page.locator(".chat-user").getByText("探索人物的动机", { exact: true }),
  ).toBeVisible();
});

test("真实接口：首次登录、创建与确认、外发、发布导出和协作权限", async ({
  page,
  request,
  browser,
}) => {
  await api(request, "/admin/bootstrap", "POST", {
    username: "browseradmin",
    password: "Browser-password-123",
  });
  await api(request, "/auth/login", "POST", {
    username: "browseradmin",
    password: "Browser-password-123",
  });
  await api(request, "/auth/change-password", "POST", {
    current_password: "Browser-password-123",
    new_password: "Browser-password-123-changed",
  });
  for (const username of ["browserowner", "browserviewer", "browsereditor"])
    await api(request, "/admin/users", "POST", {
      username,
      password: "Browser-password-123",
    });
  await api(request, "/admin/model-configs", "POST", {
    display_name: "浏览器测试模型", model_name: "normal-browser", base_url: "http://127.0.0.1:8016/v1", api_key: "browser-test-key",
  });
  await login(page, "browserowner", true);
  await page
    .locator(".home-hero")
    .getByRole("button", { name: "开始新作品", exact: true })
    .click();
  await page.getByLabel("作品名称").fill("真实接口的故事");
  await page.getByRole("button", { name: "创建作品", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "真实接口的故事", exact: true }),
  ).toBeVisible();
  const workId = page.url().split("#work/")[1];
  await page.getByRole("button", { name: "查看设定", exact: true }).click();
  for (const [stage, label, value] of [
    ["创作简报", "核心主题", "寻找真相"],
    ["人物设定", "人物姓名", "沈念"],
    ["故事大纲", "故事前提", "一次迟到的相遇"],
  ]) {
    await page
      .locator(".modal-tabs")
      .getByRole("button", { name: stage })
      .click();
    await page.getByRole("button", { name: "编辑设定", exact: true }).click();
    await page.getByLabel(label, { exact: true }).fill(value);
    await page.getByRole("button", { name: "保存设定草稿" }).click();
    await page.getByRole("button", { name: "确认此阶段" }).click();
    await expect(
      page.getByRole("button", { name: "提出设定变更" }),
    ).toBeVisible();
  }
  await page.getByRole("button", { name: "回到写作", exact: true }).click();
  await page.getByRole("button", { name: "添加第一集" }).click();
  await page.getByLabel("本集标题").fill("第一封信");
  const block = page.getByLabel("片段 1 剧本块 1 内容", { exact: true });
  await block.fill("这个版本要交付。");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect(await api(page.request, `/works/${workId}/versions`)).toHaveLength(0);
  await page.getByRole("button", { name: "发布与导出" }).click();
  await page.getByRole("button", { name: "保存当前版本" }).click();
  await page.getByRole("button", { name: "发布选中版本" }).click();
  const exported = page.waitForEvent("download");
  await page.getByRole("button", { name: "Markdown", exact: true }).click();
  expect(await readFile((await (await exported).path())!, "utf8")).toContain(
    "这个版本要交付。",
  );
  await page.getByRole("button", { name: "回到写作", exact: true }).click();
  await page.getByLabel("创作对话内容").fill("人物应该怎样出场？");
  await page.getByRole("button", { name: "发送创作对话" }).click();
  await expect(
    page.getByText("第一段回复，第二段完成。", { exact: true }),
  ).toBeVisible();
  await block.fill("草稿新修改");
  await page.getByLabel("创作对话内容").fill("第二轮讨论");
  await page.getByRole("button", { name: "发送创作对话" }).click();
  await expect(
    page.getByRole("dialog", { name: "确认本次创作的发送范围" }),
  ).toHaveCount(0);
  await expect(page.locator(".chat-assistant").last()).toContainText("第二段完成");
  await expect(block).toHaveValue("草稿新修改");
  await page.getByRole("button", { name: "版本", exact: true }).click();
  await page.getByRole("button", { name: "恢复此版本" }).click();
  await page.getByRole("button", { name: "确认恢复", exact: true }).click();
  await expect(
    page.getByRole("dialog", { name: "恢复这个版本？" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "回到写作", exact: true }).click();
  await expect(block).toHaveValue("这个版本要交付。");
  await page.getByRole("button", { name: "生成本集", exact: true }).click();
  await page.getByLabel("创作方向").fill("让人物用一个动作出场");
  await page
    .getByRole("button", { name: "生成本集", exact: true })
    .last()
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".toast")).toContainText("生成完成");
  expect(await api(page.request, `/works/${workId}/versions`)).toHaveLength(3);
  await page.getByRole("button", { name: "协作", exact: true }).click();
  for (const [username, role] of [
    ["browserviewer", "VIEWER"],
    ["browsereditor", "EDITOR"],
  ]) {
    await page.getByLabel("邀请已有账号").fill(username);
    await page.getByLabel("邀请角色").selectOption(role);
    await page.getByRole("button", { name: "发送邀请", exact: true }).click();
    await expect(
      page.locator(".member-list").getByText(username, { exact: true }),
    ).toBeVisible();
  }
  await page.getByRole("button", { name: "完成", exact: true }).click();
  for (const username of ["browserviewer", "browsereditor"]) {
    const context = await browser.newContext();
    const collaborator = await context.newPage();
    await login(collaborator, username, true);
    await collaborator
      .getByRole("button", { name: "团队协作", exact: true })
      .click();
    await collaborator.getByRole("button", { name: "接受邀请" }).click();
    await collaborator
      .getByRole("button", { name: "打开真实接口的故事", exact: true })
      .click();
    await expect(
      collaborator.getByRole("button", { name: "发布与导出" }),
    ).toHaveCount(0);
    if (username === "browserviewer") {
      await expect(
        collaborator.getByLabel("片段 1 剧本块 1 内容", { exact: true }),
      ).toHaveAttribute("readonly", "");
      await expect(
        collaborator.getByRole("button", { name: "添加一集" }),
      ).toHaveCount(0);
    } else {
      await collaborator
        .getByLabel("片段 1 剧本块 1 内容", { exact: true })
        .fill("协作修改");
      await collaborator
        .getByRole("button", { name: "保存草稿", exact: true })
        .click();
      await collaborator
        .getByRole("button", { name: "版本", exact: true })
        .click();
      await expect(
        collaborator.getByRole("button", { name: "保存当前版本" }),
      ).toHaveCount(0);
    }
    await context.close();
  }
  await block.fill("与协作者产生冲突的修改");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("作品已有新修改");
  await expect(block).toHaveValue("与协作者产生冲突的修改");
  await page.getByRole("button", { name: "使用服务器草稿" }).click();
  await expect(block).toHaveValue("协作修改");
});

test("作品首页：搜索筛选、动态模板字段、回收站恢复及手机布局", async ({
  page,
}) => {
  await page.goto("/?demo=1");
  await expect(page.locator(".work-card")).toHaveCount(3);
  await screenshot(page, "works-home");
  await page.getByLabel("搜索作品").fill("雨夜");
  await expect(page.locator(".work-card")).toHaveCount(1);
  await page.getByLabel("搜索作品").clear();
  await page.getByRole("button", { name: "已发布", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "打开潮汐之间", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".work-card")).toHaveCount(1);
  await page.getByRole("button", { name: "全部作品", exact: true }).click();
  await page.getByRole("button", { name: "剧本模板", exact: true }).click();
  await page.getByRole("button", { name: "用此模板创作" }).first().click();
  await page.getByLabel("作品名称").fill("新的故事");
  await page.getByLabel("题材标签").pressSequentially("都市\n悬疑");
  await page.getByRole("button", { name: "创建作品", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "新的故事", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "返回我的作品" }).click();
  await expect(page.getByText("都市 / 悬疑", { exact: true })).toHaveCount(2);
  await page.getByRole("button", { name: "将新的故事移入回收站" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "移入回收站", exact: true })
    .click();
  await page.getByRole("button", { name: "回收站", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "恢复新的故事", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "回收站是空的" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "我的作品", exact: true }).click();
  await expect(page.locator(".work-card")).toHaveCount(4);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await screenshot(page, "works-mobile");
});

test("管理员界面配置账号、独立模型、模板和加密备份", async ({
  page,
  request,
}) => {
  // Self-contained setup; reuse the initial administrator if another flow created it.
  const response = await request.post("/api/v1/admin/bootstrap", {
    data: { username: "browseradmin", password: "Browser-password-123" },
  });
  if (response.status() === 201) {
    await api(request, "/auth/login", "POST", {
      username: "browseradmin",
      password: "Browser-password-123",
    });
    await api(request, "/auth/change-password", "POST", {
      current_password: "Browser-password-123",
      new_password: "Browser-password-123-changed",
    });
  }
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("browseradmin");
  await page
    .getByLabel("密码", { exact: true })
    .fill("Browser-password-123-changed");
  await page.getByRole("button", { name: "进入创作空间" }).click();
  await page.getByRole("button", { name: "账户设置" }).click();
  await page.getByRole("button", { name: "平台管理", exact: true }).click();
  await page.getByRole("button", { name: "新建账号管理" }).click();
  await page.getByLabel("登录名").fill("ui-writer");
  await page
    .getByLabel("初始密码", { exact: true })
    .fill("Browser-password-123");
  await page.getByRole("button", { name: "保存配置", exact: true }).click();
  const writer = page.locator(".job-row").filter({ hasText: "ui-writer" });
  await expect(writer).toContainText("普通用户");
  await writer.getByRole("button", { name: "停用", exact: true }).click();
  await expect(writer).toContainText("停用");
  await writer.getByRole("button", { name: "启用", exact: true }).click();
  await expect(page.getByRole("button", { name: "模型供应商", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "模型配置", exact: true }).click();
  for (const suffix of ["A", "B"]) {
    await page.getByRole("button", { name: "新建模型配置" }).click();
    await page.getByLabel("显示名称", { exact: true }).fill(`界面模型 ${suffix}`);
    await expect(page.getByLabel("接口类型")).toHaveValue("openai");
    await page.getByLabel("模型标识").fill(`ui-model-${suffix}`);
    await page.getByLabel("base_url（留空使用官方默认地址）").fill("http://127.0.0.1:8999/v1");
    await page.getByLabel("API key（留空保持现有密钥）").fill("not-a-real-secret");
    await page.getByRole("button", { name: "保存配置", exact: true }).click();
  }
  const modelA = page.locator(".job-row").filter({ hasText: "界面模型 A" });
  const modelB = page.locator(".job-row").filter({ hasText: "界面模型 B" });
  await expect(modelA).toContainText("ACTIVE");
  await expect(modelB).toContainText("ACTIVE");
  await modelB.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel("API key（留空保持现有密钥）")).toHaveValue("");
  await page.getByLabel("显示名称", { exact: true }).fill("界面模型 B 新版");
  await page.getByLabel("接口类型").selectOption("anthropic");
  await page.getByRole("button", { name: "保存配置", exact: true }).click();
  const newest = page.locator(".job-row").filter({ hasText: "界面模型 B 新版" });
  await expect(newest).toContainText("v2");
  await expect(modelA).toContainText("ACTIVE");
  await newest.getByRole("button", { name: "停用", exact: true }).click();
  await expect(newest.getByRole("button", { name: "启用", exact: true })).toBeVisible();
  await expect(modelA.getByRole("button", { name: "停用", exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "剧本模板", exact: true })
    .last()
    .click();
  await page.getByRole("button", { name: "新建剧本模板" }).click();
  await page.getByLabel("模板名称").fill("界面测试模板");
  await page.getByLabel("模板标识").fill("ui-template");
  await page.getByRole("button", { name: "保存模板草稿", exact: true }).click();
  await page
    .locator(".job-row")
    .filter({ hasText: "界面测试模板" })
    .getByRole("button", { name: "发布模板", exact: true })
    .click();
  await expect(
    page.locator(".job-row").filter({ hasText: "界面测试模板" }),
  ).toContainText("已发布 v1");
  await page.getByRole("button", { name: "审计与备份" }).click();
  await page.getByRole("button", { name: "创建备份", exact: true }).click();
  await expect(
    page.getByText("加密备份已生成。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "恢复备份", exact: true }),
  ).toHaveCount(1);
  await page.getByRole("button", { name: "返回创作", exact: true }).click();
});

test("自动保存不创建版本，作品参数与场景生成可操作", async ({ page }) => {
  await page.goto("/?demo=1#work/demo-0");
  await page.getByLabel("本集标题").fill("自动保存后的标题");
  await expect(page.locator(".save-status")).toContainText("已自动保存", {
    timeout: 8000,
  });
  const state = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("mujian-demo-v2")!),
  );
  expect(state[0].versions).toHaveLength(1);
  await page.reload();
  await expect(page.getByLabel("本集标题")).toHaveValue("自动保存后的标题");
  await page.getByRole("button", { name: "查看设定", exact: true }).click();
  await page.getByRole("button", { name: "编辑作品参数" }).click();
  await page.getByLabel("作品名称").fill("新的作品名称");
  await page.getByRole("button", { name: "保存作品参数" }).click();
  await page.getByRole("button", { name: "回到写作", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "新的作品名称", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "生成本集", exact: true }).click();
  await page.getByLabel("生成范围").selectOption("SCENE");
  await page.getByLabel("创作方向").fill("生成一个安静的场景");
  await page.getByRole("button", { name: "生成场景", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".toast")).toContainText("生成完成");
  const after = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("mujian-demo-v2")!),
  );
  expect(after[0].versions).toHaveLength(2);
});

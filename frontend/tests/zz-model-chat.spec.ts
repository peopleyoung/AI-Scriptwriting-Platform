import { test, expect, type APIRequestContext, type Page } from '@playwright/test';

const password = 'Browser-password-123';
async function api(request: APIRequestContext, path: string, method = 'GET', data?: unknown) {
  const csrf = (await request.storageState()).cookies.find((c) => c.name === 'scriptwriting_csrf')?.value;
  const result = await request.fetch(`/api/v1${path}`, { method, data, headers: csrf ? { 'X-CSRF-Token': csrf } : {} });
  expect(result.ok(), await result.text()).toBeTruthy();
  return result.status() === 204 ? undefined : result.json();
}
async function setup(page: Page, suffix: string) {
  const request = page.request;
  const boot = await request.post('/api/v1/admin/bootstrap', { data: { username: 'browseradmin', password } });
  await api(request, '/auth/login', 'POST', { username: 'browseradmin', password: boot.status() === 201 ? password : password + '-changed' });
  if (boot.status() === 201) await api(request, '/auth/change-password', 'POST', { current_password: password, new_password: password + '-changed' });
  await request.post('http://127.0.0.1:8016/reset');
  const a = await api(request, '/admin/model-configs', 'POST', { display_name: '增量模型A-' + suffix, model_name: 'hold-openai', base_url: 'http://127.0.0.1:8016/v1', api_key: 'browser-a' });
  const b = await api(request, '/admin/model-configs', 'POST', { display_name: '增量模型B-' + suffix, model_name: 'normal-anthropic', adapter: 'anthropic', base_url: 'http://127.0.0.1:8016/v1', api_key: 'browser-b' });
  const work = await api(request, '/works', 'POST', { title: '连续创作-' + suffix });
  const draft = { episodes: [1, 2].map((n) => ({ number: n, title: `集${n}`, scenes: [{ number: 1, heading: '夜', fragments: [{ id: `f${n}`, blocks: [{ type: 'ACTION', text: `独特正文${n}` }] }] }] })) };
  await api(request, `/works/${work.id}/draft`, 'PATCH', { content: draft });
  await page.goto(`/#work/${work.id}`);
  await expect(page.getByRole('heading', { name: work.title, exact: true })).toBeVisible();
  await page.getByLabel('创作模型', { exact: true }).selectOption(a.model_id);
  return { a, b, work, request };
}

test('真实增量、正文继续编辑、停止与刷新、跨模型历史及个人偏好', async ({ page, browser }) => {
  test.setTimeout(90000);
  const { a, b, work, request } = await setup(page, 'stream');
  const input = page.getByLabel('创作对话内容');
  await input.fill('第一轮创作讨论');
  await page.getByRole('button', { name: '发送创作对话', exact: true }).click();
  await expect(page.getByText('第一轮创作讨论', { exact: true })).toBeVisible();
  // Upstream is deliberately held before its final chunk: this proves incrementality.
  await expect(page.locator('.chat-assistant').last()).toContainText('第一段回复');
  await expect(page.locator('.chat-assistant').last()).not.toContainText('第二段完成');
  await expect(page.getByRole('dialog', { name: '确认本次创作的发送范围' })).toHaveCount(0);
  const block = page.getByLabel('片段 1 剧本块 1 内容', { exact: true });
  await block.fill('等待回复时保存的新正文');
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect(page.locator('.save-status')).toContainText('已保存');
  // Selection changes only the next request.
  await page.getByLabel('创作模型', { exact: true }).selectOption(b.model_id);
  await request.post('http://127.0.0.1:8016/release');
  await expect(page.locator('.chat-assistant').last()).toContainText('第二段完成');
  await expect(page.locator('.chat-assistant').last()).toContainText(a.display_name);
  await expect(page.getByRole('button', { name: '停止回复', exact: true })).toHaveCount(0);
  await input.fill('切换后继续讨论');
  await page.getByRole('button', { name: '发送创作对话', exact: true }).click();
  await expect(page.locator('.conversation')).toHaveCount(2);
  await expect(page.locator('.chat-assistant').last()).toContainText(b.display_name);
  await expect(page.locator('.chat-assistant').last()).toContainText('第二段完成');
  const sent = await (await request.get('http://127.0.0.1:8016/requests')).json();
  expect(sent).toHaveLength(2);
  expect(sent[1].path).toBe('/v1/messages');
  expect(JSON.stringify(sent[1].body)).toContain('第一轮创作讨论');
  expect(JSON.stringify(sent[0].body)).not.toContain('独特正文');
  expect(JSON.stringify(sent[1].body)).toContain('等待回复时保存的新正文');
  await page.getByLabel('包含整部正文').check();
  await page.reload();
  await expect(page.getByLabel('创作模型', { exact: true })).toHaveValue(b.model_id);
  await expect(page.getByLabel('包含整部正文')).toBeChecked();
  await expect(page.getByLabel('片段 1 剧本块 1 内容', { exact: true })).toHaveValue('等待回复时保存的新正文');
  // Another member's preference is independent in a separate browser context.
  const user = await api(request, '/admin/users', 'POST', { username: 'stream-editor', password, role: 'WRITER' });
  await api(request, `/works/${work.id}/members/invitations`, 'POST', { user_id: user.id, role: 'EDITOR' });
  const context = await browser.newContext({ baseURL: new URL(page.url()).origin });
  const editor = await context.newPage();
  await api(editor.request, '/auth/login', 'POST', { username: 'stream-editor', password });
  await api(editor.request, '/auth/change-password', 'POST', { current_password: password, new_password: password + '-changed' });
  await api(editor.request, `/works/${work.id}/members/accept`, 'POST');
  await editor.goto(`/#work/${work.id}`);
  await editor.getByLabel('创作模型', { exact: true }).selectOption(a.model_id);
  await editor.reload();
  await expect(editor.getByLabel('创作模型', { exact: true })).toHaveValue(a.model_id);
  await expect(editor.getByLabel('包含整部正文')).not.toBeChecked();
  await expect(page.getByLabel('创作模型', { exact: true })).toHaveValue(b.model_id);
  // A fresh held call can be stopped, reloaded and excluded from future history.
  await request.post('http://127.0.0.1:8016/reset');
  await editor.getByLabel('创作对话内容').fill('将停止的讨论');
  await editor.getByRole('button', { name: '发送创作对话', exact: true }).click();
  await expect(editor.locator('.chat-assistant').last()).toContainText('第一段回复');
  await expect(page.getByText(/作品已有成员正在进行创作对话/)).toBeVisible();
  await editor.getByRole('button', { name: '停止回复', exact: true }).click();
  await expect(editor.locator('.chat-assistant').last()).toContainText('已停止');
  await request.post('http://127.0.0.1:8016/release');
  await editor.reload();
  await expect(editor.locator('.chat-assistant').last()).toContainText('第一段回复');
  await expect(editor.locator('.chat-assistant').last()).toContainText('已停止');
  await api(request, `/admin/model-configs/${a.id}`, 'PATCH', { enabled: false });
  await expect(editor.getByText('所选模型不可用，请重新选择；仍可继续手动写作。')).toBeVisible();
  await expect(editor.getByLabel('创作模型', { exact: true })).toHaveValue(a.model_id);
  await context.close();
});

test('原生 Anthropic 增量、断线保存与手动重试，不自动重新发送', async ({ page }) => {
  const { b, work, request } = await setup(page, 'disconnect');
  const held = await api(request, `/admin/model-configs/${b.id}`, 'PATCH', { model_name: 'hold-anthropic' });
  await page.reload();
  await page.getByLabel('创作模型', { exact: true }).selectOption(b.model_id);
  await page.getByLabel('创作对话内容').fill('断线时的讨论');
  await page.getByRole('button', { name: '发送创作对话', exact: true }).click();
  await expect(page.locator('.chat-assistant').last()).toContainText('第一段回复');
  await expect(page.locator('.chat-assistant').last()).not.toContainText('第二段完成');
  await page.reload();
  await expect(page.locator('.chat-assistant').last()).toContainText('未完成');
  await expect(page.locator('.chat-assistant').last()).toContainText('第一段回复');
  expect(await (await request.get('http://127.0.0.1:8016/requests')).json()).toHaveLength(1);
  await request.post('http://127.0.0.1:8016/release');
  await api(request, `/admin/model-configs/${held.id}`, 'PATCH', { model_name: 'drop-anthropic' });
  await expect.poll(async () => (await api(request, '/model-configs')).find((m: { model_id: string }) => m.model_id === b.model_id)?.model_name).toBe('drop-anthropic');
  await page.reload();
  await page.getByRole('button', { name: '重试对话', exact: true }).click();
  await expect(page.locator('.conversation')).toHaveCount(2);
  await expect(page.locator('.chat-assistant').last()).toContainText('未完成');
  await expect(page.getByLabel('创作对话内容')).toHaveValue('断线时的讨论');
  const requests = await (await request.get('http://127.0.0.1:8016/requests')).json();
  expect(requests).toHaveLength(2);
  expect(requests[1].path).toBe('/v1/messages');
  // Only the retried current message is sent, never its incomplete predecessor.
  expect(requests[1].body.messages).toHaveLength(1);
});

test('所有者和编辑者直接生成与选段重写，保留整部作品版本', async ({ page, browser }) => {
  const { b, work, request } = await setup(page, 'generate');
  for (const [stage, content] of [['brief', { theme: '勇气' }], ['characters', [{ name: '主角' }]], ['outline', { premise: '寻找真相' }]] as const) {
    await api(request, `/works/${work.id}/creative/${stage}`, 'PATCH', content);
    await api(request, `/works/${work.id}/creative/${stage}/confirm`, 'POST');
  }
  await page.reload();
  await page.getByLabel('创作模型', { exact: true }).selectOption(b.model_id);
  await page.getByRole('button', { name: '生成本集', exact: true }).click();
  await page.getByLabel('创作方向').fill('推进主角冲突');
  await page.getByRole('dialog').getByRole('button', { name: '生成本集', exact: true }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByLabel('片段 1 剧本块 1 内容', { exact: true })).toHaveValue('HTTP生成正文');
  let versions = await api(request, `/works/${work.id}/versions`);
  expect(versions).toHaveLength(1);
  expect(versions[0].content.episodes[0].scenes[0].fragments[0].blocks[0].text).toBe('独特正文1');
  const user = await api(request, '/admin/users', 'POST', { username: 'generation-editor', password, role: 'WRITER' });
  await api(request, `/works/${work.id}/members/invitations`, 'POST', { user_id: user.id, role: 'EDITOR' });
  const context = await browser.newContext({ baseURL: new URL(page.url()).origin });
  const editor = await context.newPage();
  await api(editor.request, '/auth/login', 'POST', { username: 'generation-editor', password });
  await api(editor.request, '/auth/change-password', 'POST', { current_password: password, new_password: password + '-changed' });
  await api(editor.request, `/works/${work.id}/members/accept`, 'POST');
  await editor.goto(`/#work/${work.id}`);
  await editor.getByLabel('创作模型', { exact: true }).selectOption(b.model_id);
  await editor.getByLabel('片段 1 剧本块 1 内容', { exact: true }).click();
  await editor.getByRole('button', { name: '重写片段', exact: true }).click();
  await editor.getByLabel('创作方向').fill('重写片段冲突');
  await editor.getByRole('button', { name: '重写选中片段', exact: true }).click();
  await expect(editor.getByRole('dialog')).toHaveCount(0);
  await expect.poll(async () => (await api(request, `/works/${work.id}/versions`)).length).toBe(2);
  versions = await api(request, `/works/${work.id}/versions`);
  expect(versions[1].content.episodes[0].scenes[0].fragments[0].blocks[0].text).toBe('独特正文1');
  await context.close();
});

test('选中正文与整部范围实际发送一致，管理员停用与页面备份恢复保留历史', async ({ page }) => {
  test.setTimeout(90000);
  const { a, b, work, request } = await setup(page, 'scope-backup');
  await page.getByLabel('创作模型', { exact: true }).selectOption(b.model_id);
  await page.getByLabel('片段 1 剧本块 1 内容', { exact: true }).click();
  await expect(page.getByLabel('对话正文范围')).toHaveValue('FRAGMENT');
  await page.getByLabel('创作对话内容').fill('讨论选中的正文');
  await page.getByRole('button', { name: '发送创作对话', exact: true }).click();
  await expect(page.locator('.chat-assistant').last()).toContainText('第二段完成');
  let sent = await (await request.get('http://127.0.0.1:8016/requests')).json();
  expect(JSON.stringify(sent[0].body)).toContain('独特正文2');
  expect(JSON.stringify(sent[0].body)).not.toContain('独特正文1');
  await page.getByLabel('包含整部正文').check();
  await page.getByLabel('创作对话内容').fill('讨论整部正文');
  await page.getByRole('button', { name: '发送创作对话', exact: true }).click();
  await expect(page.locator('.conversation')).toHaveCount(2);
  await expect(page.locator('.chat-assistant').last()).toContainText('第二段完成');
  sent = await (await request.get('http://127.0.0.1:8016/requests')).json();
  expect(JSON.stringify(sent[1].body)).toContain('独特正文1');
  expect(JSON.stringify(sent[1].body)).toContain('独特正文2');
  await page.getByLabel('创作模型', { exact: true }).selectOption(a.model_id);
  await page.getByLabel('创作对话内容').fill('管理员将停止这次回复');
  await page.getByRole('button', { name: '发送创作对话', exact: true }).click();
  await expect(page.locator('.chat-assistant').last()).toContainText('第一段回复');
  const management = await page.context().newPage();
  await management.goto('/');
  await management.getByRole('button', { name: '账户设置' }).click();
  await management.getByRole('button', { name: '平台管理', exact: true }).click();
  await management.getByRole('button', { name: '模型配置', exact: true }).click();
  await management.locator('.job-row').filter({ hasText: a.display_name }).getByRole('button', { name: '停用', exact: true }).click();
  await expect(page.locator('.chat-assistant').last()).toContainText('已停止');
  await expect(page.getByText('所选模型不可用，请重新选择；仍可继续手动写作。')).toBeVisible();
  await request.post('http://127.0.0.1:8016/release');
  await management.getByRole('button', { name: '审计与备份' }).click();
  await management.getByRole('button', { name: '创建备份', exact: true }).click();
  await expect(management.getByText('加密备份已生成。', { exact: true })).toBeVisible();
  const backups = await api(request, '/admin/backups');
  await api(request, `/works/${work.id}/draft`, 'PATCH', { content: { episodes: [] } });
  await management.locator('.job-row').filter({ hasText: backups[0].checksum.slice(0, 16) }).getByRole('button', { name: '恢复备份', exact: true }).click();
  await management.getByRole('button', { name: '确认恢复备份', exact: true }).click();
  await expect.poll(async () => (await api(request, `/works/${work.id}/draft`)).content.episodes.length).toBe(2);
  await page.reload();
  await expect(page.locator('.conversation')).toHaveCount(3);
  await expect(page.locator('.chat-assistant').last()).toContainText('已停止');
  await expect(page.locator('.chat-assistant').last()).toContainText(a.display_name);
  await expect(page.getByLabel('片段 1 剧本块 1 内容', { exact: true })).toHaveValue('独特正文2');
  await management.close();
});

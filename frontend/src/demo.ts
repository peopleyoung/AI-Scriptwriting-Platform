import type { Creative, Script, Template, Work } from "./types";

export const demoUser = {
  id: "demo-writer",
  username: "林一",
  role: "WRITER",
  must_change_password: false,
};
const definition = {
  settings: {
    episode_count: { type: "integer", min: 1, max: 200 },
    episode_duration_minutes: { type: "integer", min: 1, max: 120 },
    genre_tags: { type: "array", items: { type: "string" } },
    output_format: { type: "enum", values: ["markdown", "docx"] },
  },
  creative_brief: {
    fields: Object.fromEntries(
      ["theme", "tone", "logline", "target_audience", "world_setting"].map(
        (key) => [key, { type: "string", max_length: 4000 }],
      ),
    ),
  },
  characters: {
    item_fields: Object.fromEntries(
      ["name", "role", "traits", "goal", "relationship"].map((key) => [
        key,
        { type: "string", required: key === "name" },
      ]),
    ),
  },
  outline: {
    fields: {
      premise: { type: "string" },
      beginning: { type: "string" },
      middle: { type: "string" },
      ending: { type: "string" },
      episode_summaries: { type: "array", items: { type: "string" } },
    },
  },
  script_structure: {
    blocks: ["SCENE_HEADING", "ACTION", "CHARACTER", "DIALOGUE"],
  },
};
export const demoTemplates: Template[] = [
  {
    id: "vertical",
    name: "竖屏微短剧",
    slug: "vertical-micro-drama",
    published_version: { id: "vertical-v1", definition },
  },
  {
    id: "web",
    name: "网剧短剧",
    slug: "web-short-drama",
    published_version: { id: "web-v1", definition },
  },
];
export const sampleCreative: Creative = {
  brief: {
    theme: "记忆与真相，人与人之间迟到的理解。",
    tone: "克制、悬疑，有温度的都市叙事。",
    logline:
      "一位失去部分记忆的年轻修复师，在每个雨夜都会收到一封没有署名的信。循着信里的线索，她发现寄信人竟与三年前的自己有关。",
    target_audience: "18—35 岁，喜欢都市悬疑与情感故事的观众。",
    world_setting:
      "当代南方小城。老城区拆迁在即，一间旧照相馆承载着被遗忘的秘密。",
  },
  characters: [
    {
      name: "沈念",
      role: "女主角 · 28 岁 · 照片修复师",
      traits: "敏锐、独立，习惯用沉默保护自己。",
      goal: "找回三年前失去的记忆。",
      relationship: "与陆川有一段被遗忘的共同过去。",
    },
    {
      name: "陆川",
      role: "男主角 · 31 岁 · 旧书店店主",
      traits: "温和寡言，对往事避而不谈。",
      goal: "守住一个承诺，又想让沈念知道真相。",
      relationship: "认识失忆前的沈念，是匿名信的关键线索。",
    },
    {
      name: "周姨",
      role: "配角 · 56 岁 · 照相馆老板",
      traits: "直率热心，保留着老城区的所有故事。",
      goal: "在拆迁前替故人完成一件事。",
      relationship: "沈念的师傅，也是往事的见证者。",
    },
  ],
  outline: {
    premise: "一封雨夜来信，将修复师沈念带回三年前的失踪事件。",
    beginning: "匿名信出现。沈念来到信中提到的旧照相馆，发现自己的照片。",
    middle: "陆川试图阻止她寻找真相。一张修复后的底片，揭示了完全不同的过去。",
    ending: "最后一封信终于有了署名。沈念选择面对过去，也选择重新开始。",
    episode_summaries: [
      "第一封来信：雨夜，门缝里出现一封湿透的信。",
      "旧照相馆：照片背面写着一个熟悉的名字。",
      "被遗忘的门牌：沈念循着照片找到陆川。",
    ],
  },
  confirmed: {},
};
export function sampleScript(title = "雨夜来信", episode = 3): Script {
  return {
    episodes: [
      {
        number: episode,
        title:
          ["第一封来信", "旧照相馆", "被遗忘的门牌"][episode - 1] ||
          "一封迟到的信",
        scenes: [
          {
            number: 1,
            heading: "旧书店外 · 夜 · 雨",
            fragments: [
              {
                id: "opening",
                blocks: [
                  { type: "SCENE_HEADING", text: "外景 · 旧书店 · 夜" },
                  {
                    type: "ACTION",
                    text: "雨水顺着老街的屋檐落下。沈念站在一块褪色的门牌前，手里攥着那封已经被雨浸湿的信。远处，最后一班公交缓缓驶离。",
                  },
                  {
                    type: "ACTION",
                    text: "书店的灯还亮着。玻璃窗后，一个男人正在整理书架。沈念抬头，目光停在门牌上的数字——17。",
                  },
                  { type: "CHARACTER", text: "沈念" },
                  {
                    type: "DIALOGUE",
                    text: "（轻声，像是在确认什么）\n就是这里。",
                  },
                  {
                    type: "ACTION",
                    text: "她推开门。门上的铜铃响了一声。男人的手停在半空，却没有立刻回头。",
                  },
                  { type: "CHARACTER", text: "陆川" },
                  { type: "DIALOGUE", text: "今天已经打烊了。" },
                  { type: "CHARACTER", text: "沈念" },
                  {
                    type: "DIALOGUE",
                    text: "我不是来买书的。\n有人让我，把这封信交给你。",
                  },
                  {
                    type: "ACTION",
                    text: `陆川终于转身。他看见沈念的那一刻，脸上的平静裂开了一道缝。窗外的雨声突然变得很远。\n\n信纸背面，一行模糊的字迹浮现：${title === "雨夜来信" ? "“如果你忘了，就从这里开始。”" : "“我们会在下一次相遇时，认出彼此。”"}`,
                  },
                ],
              },
            ],
          },
          {
            number: 2,
            heading: "旧书店内 · 夜",
            fragments: [
              {
                id: "inside",
                blocks: [
                  { type: "SCENE_HEADING", text: "内景 · 旧书店 · 夜" },
                  {
                    type: "ACTION",
                    text: "陆川把信放在台灯下。暖黄的灯光照着两人的侧脸，隔在他们之间的，是长达三年的沉默。",
                  },
                  { type: "CHARACTER", text: "陆川" },
                  { type: "DIALOGUE", text: "你真的……一点都不记得了？" },
                ],
              },
            ],
          },
        ],
      },
    ],
  };
}
export function seedWorks(): Work[] {
  const now = new Date().toISOString();
  return ["雨夜来信", "第二次心动", "潮汐之间"].map((title, i) => {
    const creative = structuredClone(sampleCreative);
    if (i === 1) {
      creative.brief!.logline =
        "两个总在错误时间相遇的人，决定给彼此第二次机会。";
      creative.confirmed = { brief: creative.brief };
    } else
      creative.confirmed = {
        brief: creative.brief,
        characters: creative.characters,
        outline: creative.outline,
      };
    const draft =
      i === 1
        ? {}
        : i === 0
          ? {
              episodes: [1, 2, 3].map(
                (n) => sampleScript(title, n).episodes![0],
              ),
            }
          : sampleScript(title, 1);
    const version = {
      id: `version-${i}-1`,
      source: "MANUAL_SAVE",
      content: structuredClone(draft),
      created_at: now,
      work_revision: 1,
      author_display_name: demoUser.username,
    };
    return {
      id: `demo-${i}`,
      title,
      owner_id: i === 2 ? "demo-member" : demoUser.id,
      revision: 2,
      status: "ACTIVE",
      updated_at: new Date(Date.now() - i * 7200000).toISOString(),
      settings: {
        episode_count: i === 0 ? 12 : i === 1 ? 24 : 8,
        episode_duration_minutes: i === 2 ? 12 : 3,
        genre_tags:
          i === 0
            ? ["都市", "悬疑"]
            : i === 1
              ? ["爱情", "轻喜剧"]
              : ["青春", "治愈"],
        output_format: "markdown",
      },
      templateName: i === 2 ? "网剧短剧" : "竖屏微短剧",
      template_version_id: i === 2 ? "web-v1" : "vertical-v1",
      art: ["rain", "sunset", "ocean"][i],
      template_definition: definition,
      role: i === 2 ? "EDITOR" : "OWNER",
      creative,
      draft,
      versions: i === 1 ? [] : [version],
      releases:
        i === 2
          ? [
              {
                id: "release-demo",
                version_id: version.id,
                is_current: true,
                created_at: now,
                version,
              },
            ]
          : [],
      conversations:
        i === 0
          ? [
              {
                id: "welcome",
                user_message:
                  "我想让沈念和陆川的第一次见面更有悬念，但不要直接揭示他们的关系。",
                assistant_message:
                  "可以让“认出”成为这个场景的暗线。\n\n沈念带着信走进书店，陆川先认出她，却把情绪藏在一句普通的“打烊了”里。观众能感受到他的异常，沈念却还毫不知情。\n\n建议保留三个细节：停在半空的手、迟迟不回头的背影，以及看到信时的沉默。让动作替对白说出秘密。",
                created_at: now,
              },
            ]
          : [],
      proposals: [],
      members: [
        {
          id: "member-owner",
          user_id: i === 2 ? "demo-member" : demoUser.id,
          username: i === 2 ? "陈默" : "林一",
          role: "OWNER",
          status: "ACCEPTED",
        },
        ...(i === 2
          ? [
              {
                id: "member-you",
                user_id: demoUser.id,
                username: "林一",
                role: "EDITOR" as const,
                status: "ACCEPTED",
              },
            ]
          : []),
      ],
    };
  });
}

export type Stage = "brief" | "characters" | "outline";
export type MemberRole = "OWNER" | "EDITOR" | "VIEWER";
export type Page = "works" | "team" | "templates" | "trash";
export interface User {
  id: string;
  username: string;
  role: string;
  must_change_password: boolean;
}
export interface Block {
  type: string;
  text: string;
}
export interface Fragment {
  id: string;
  blocks: Block[];
}
export interface Scene {
  number: number;
  heading: string;
  fragments: Fragment[];
}
export interface Episode {
  number: number;
  title: string;
  scenes: Scene[];
}
export interface Script {
  episodes?: Episode[];
}
export interface Character {
  name: string;
  role?: string;
  traits?: string;
  goal?: string;
  relationship?: string;
  [key: string]: unknown;
}
export interface Brief {
  theme?: string;
  tone?: string;
  logline?: string;
  target_audience?: string;
  world_setting?: string;
  [key: string]: unknown;
}
export interface Outline {
  premise?: string;
  beginning?: string;
  middle?: string;
  ending?: string;
  episode_summaries?: string[];
  [key: string]: unknown;
}
export interface Creative {
  brief: Brief | null;
  characters: Character[] | null;
  outline: Outline | null;
  confirmed: Partial<Record<Stage, unknown>>;
}
export interface Version {
  id: string;
  source: string;
  content: Script;
  created_at: string;
  work_revision: number;
}
export interface Release {
  id: string;
  version_id: string;
  is_current: boolean;
  created_at: string;
  version: Version;
}
export interface Message {
  id: string;
  user_message: string;
  assistant_message: string | null;
  created_at: string;
}
export interface Proposal {
  id: string;
  stage: Stage;
  content: Brief | Character[] | Outline;
  status: string;
}
export interface Member {
  id: string;
  user_id: string;
  username: string;
  role: MemberRole;
  status: string;
}
export interface Work {
  id: string;
  title: string;
  owner_id: string;
  revision: number;
  status: string;
  updated_at: string;
  settings: {
    episode_count?: number;
    episode_duration_minutes?: number;
    episode_word_target?: number;
    genre_tags?: string[];
    output_format?: string;
  };
  template_version_id?: string;
  templateName: string;
  role: MemberRole;
  art: string;
  creative: Creative;
  draft: Script;
  versions: Version[];
  releases: Release[];
  conversations: Message[];
  proposals: Proposal[];
  members: Member[];
  template_definition?: Definition;
}
export interface Template {
  id: string;
  name: string;
  slug: string;
  published_version: { id: string; definition: Record<string, unknown> } | null;
}
export interface Model {
  id: string;
  provider_id: string;
  model_name: string;
  version: number;
  parameters: Record<string, unknown>;
}
export interface Provider {
  id: string;
  name: string;
  adapter: string;
  retention_description: string | null;
  deletion_description: string | null;
  incident_description: string | null;
}
export interface Consent {
  id: string;
  input_revision: number;
  model_config_version_id: string;
  data_categories: string[];
  status: string;
}
export interface NewWork {
  title: string;
  template_id: string;
  settings: Work["settings"];
}
export const stages: Stage[] = ["brief", "characters", "outline"];
export const stageNames: Record<Stage, string> = {
  brief: "创作简报",
  characters: "人物设定",
  outline: "故事大纲",
};
export const roleNames: Record<MemberRole, string> = {
  OWNER: "所有者",
  EDITOR: "编辑者",
  VIEWER: "查看者",
};
export interface Invitation {
  work_id: string;
  title: string;
  role: "EDITOR" | "VIEWER";
  invited_at: string;
}
export interface Rule {
  type?: string;
  required?: boolean;
  min?: number;
  max?: number;
  max_length?: number;
  values?: string[];
  items?: Rule;
  fields?: Record<string, Rule>;
}
export type Definition = Record<string, unknown>;
export function definitionFields(
  definition: Definition,
  section: "settings" | Stage,
): Record<string, Rule> {
  const aliases = {
    settings: [
      "settings",
      "settings_schema",
      "settings_fields",
      "work_settings",
    ],
    brief: ["creative_brief", "creative_brief_fields", "brief_fields"],
    characters: ["characters", "character_fields"],
    outline: ["outline", "outline_fields"],
  };
  let raw: unknown = aliases[section]
    .map((key) => definition[key])
    .find((value) => value !== undefined);
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const value = raw as Record<string, unknown>;
    raw = value.item_fields || value.fields || raw;
  }
  if (Array.isArray(raw))
    return Object.fromEntries(
      raw.map((field) =>
        typeof field === "string"
          ? [field, { type: "string" }]
          : [field.key || field.name, field],
      ),
    );
  if (!raw || typeof raw !== "object") return {};
  return Object.fromEntries(
    Object.entries(raw).map(([key, rule]) => [
      key,
      typeof rule === "string" ? { type: rule } : (rule as Rule),
    ]),
  );
}

export interface Job {
  id: string;
  status: string;
  target_type: string;
  target_path: { episode?: number; scene?: number; fragment?: string };
  error_code: string | null;
  error_message: string | null;
  created_at: string;
}

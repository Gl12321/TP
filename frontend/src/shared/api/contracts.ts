export type Capability =
  | "analytics:read"
  | "assistant:use"
  | "reports:write"
  | "cases:write"
  | "plans:write"
  | "metrics:write"
  | "sources:manage"
  | "members:manage";
export type Role =
  | "director"
  | "regional_manager"
  | "franchise_owner"
  | "store_manager"
  | "analyst"
  | "admin";
export type User = { id: string; email: string; name: string };
export type Workspace = {
  id: string;
  name: string;
  role: Role;
  capabilities: Capability[];
  all_stores: boolean;
  store_ids: string[];
};
export type Session = {
  user: User;
  csrf_token: string;
  workspaces: Workspace[];
};
export type AuthStatus = {
  bootstrap_required: boolean;
  bootstrap_token_required: boolean;
};
export type Store = {
  id: string;
  name: string;
  code: string;
  city: string;
  owner_name: string;
  active: boolean;
};
export type Metric = {
  id: string;
  key: string;
  name: string;
  description: string;
  unit: string;
  source_id: string;
  table_schema: string;
  table_name: string;
  value_column: string | null;
  date_column: string;
  store_column: string;
  aggregation: "sum" | "count" | "avg";
  version: number;
};
export type Plan = {
  id: string;
  store_id: string;
  metric_id: string;
  period: string;
  amount: string;
  version: number;
};
export type OverviewRow = {
  store_id: string;
  name: string;
  city: string;
  actual: string | null;
  plan: string | null;
  attainment: string | number | null;
  status: string;
  previous_actual?: string | null;
  change_percent?: string | null;
};
export type Overview = {
  date_from: string;
  date_to: string;
  metric: Metric | null;
  totals: {
    actual: string | null;
    plan: string | null;
    attainment: string | number | null;
    planned_store_count?: number;
  };
  stores: OverviewRow[];
  coverage: { available: number; total: number };
  warnings: string[];
  cities?: CityOverview[];
  captured_at?: string;
  calculation?: {
    sql: string;
    execution: { sql: string; parameters: unknown[] } | null;
  };
  comparison: null | {
    date_from: string;
    date_to: string;
    current: string | null;
    previous: string | null;
    delta: string | null;
    change_percent: string | null;
    comparable_count: number;
  };
};
export type CityOverview = {
  city: string;
  store_count: number;
  available_count: number;
  actual: string | null;
  previous_actual: string | null;
  comparable_current: string | null;
  change_percent: string | null;
  plan: string | null;
  attainment: string | null;
  comparable_count: number;
};
export type Source = {
  id: string;
  name: string;
  host: string;
  port: number;
  database: string;
  username: string;
  schemas: string[];
  enabled: boolean;
  catalog_version: number;
  status: string;
  last_checked_at: string | null;
  error: string | { message: string } | null;
  ssl_mode?: "require" | "verify-full" | "disable";
  reader_ids?: string[] | null;
};
export type TablePolicy = {
  columns: string[];
  store_column: string | null;
  shared: boolean;
};
export type CatalogTable = {
  schema: string;
  name: string;
  columns: { name: string; data_type: string }[];
  policy: TablePolicy | null;
};
export type RunStatus =
  | "queued"
  | "running"
  | "cancel_requested"
  | "cancelled"
  | "succeeded"
  | "failed"
  | "needs_input"
  | "rejected";
export type RunContext = {
  date_from?: string | null;
  date_to?: string | null;
  metric_id?: string | null;
  metric_version?: number | null;
  catalog_version?: number | null;
};
export type ResultColumn = { name: string; type: string; unit?: string };
export type Result = {
  columns: ResultColumn[];
  rows: unknown[][];
  truncated: boolean;
  row_count: number;
  execution?: { sql: string; parameters: unknown[] } | null;
};
export type Run = {
  id: string;
  conversation_id: string | null;
  question: string;
  source_id: string;
  store_ids: string[];
  status: RunStatus;
  stage: string | null;
  sql: string | null;
  result: Result | null;
  error: { code: string; message: string } | null;
  clarification: { code: string; message: string } | null;
  created_at: string;
  finished_at: string | null;
  conversation_version: number;
  context?: RunContext;
};
export type Conversation = {
  id: string;
  title: string;
  version: number;
  created_at: string;
  updated_at: string;
};
export type Message = {
  id: string;
  role: string;
  content: string;
  run_id: string | null;
  created_at: string;
};
export type ConversationDetail = Conversation & {
  messages: Message[];
  runs: Run[];
};
export type Report = {
  id: string;
  title: string;
  description: string;
  run_id: string;
  created_at: string;
  created_by: string;
};
export type ReportDetail = Report & { run: Run };
export type ReportHistory = Report & {
  run: Run;
  refreshes: Run[];
  inaccessible_refreshes?: number;
};
export type MeasurementRequest = {
  metric_id: string;
  date_from: string;
  date_to: string;
};
export type Measurement = {
  id: string;
  metric: Metric;
  store_ids: string[];
  date_from: string;
  date_to: string;
  created_at: string;
  created_by: string;
  overview: Overview;
  change_from_initial?: {
    delta: string | null;
    change_percent: string | null;
    current: string | null;
    initial: string | null;
    comparable_count: number;
  };
};
export type StoreAnalytics = {
  store: Store;
  metric: Metric;
  date_from: string;
  date_to: string;
  grain: "day" | "week";
  series: {
    date: string;
    value: string | null;
    weight: string | number | null;
  }[];
  warnings: string[];
  calculation?: {
    sql: string;
    execution: { sql: string; parameters: unknown[] } | null;
  };
};
export type Case = {
  id: string;
  title: string;
  description: string;
  status: string;
  store_ids: string[];
  run_id: string | null;
  created_by: string;
  assignee_id: string | null;
  conclusion: string | null;
  created_at: string;
  updated_at: string;
  pending_for_me?: boolean;
  pending_assignee_ids?: string[];
};
export type CaseComment = {
  id: string;
  author_id: string;
  author_name: string;
  body: string;
  created_at: string;
};
export type CaseQuestion = {
  id: string;
  body: string;
  assignee_id: string;
  status: string;
  answer: string | null;
  created_at: string;
};
export type CaseDetail = Case & {
  comments: CaseComment[];
  questions: CaseQuestion[];
  measurements?: Measurement[];
};
export type Participant = { id: string; name: string; role: Role };
export type Member = {
  id: string;
  user_id: string;
  email: string;
  name: string;
  role: Role;
  all_stores: boolean;
  store_ids: string[];
  active: boolean;
  data_access: boolean;
  owner: boolean;
};
export type Notification = {
  id: string;
  kind: string;
  title: string;
  body: string;
  case_id: string | null;
  read_at: string | null;
  created_at: string;
};
export type Invitation = {
  id: string;
  email: string;
  role: Role;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
};
export const activeRun = (status: RunStatus) =>
  ["queued", "running", "cancel_requested"].includes(status);

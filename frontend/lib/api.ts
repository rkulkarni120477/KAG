export type Role = "student" | "teacher" | "advisor" | "admin";

export type SessionUser = { id: string; name: string; role: Role; email: string; tenant_id: string };
export type Student = { id: string; name: string; grade: number };
export type RequirementState = { id: string; met: boolean };
export type RequirementGroup = { group_id: string; logic: "ALL" | "ANY"; met: boolean; requires: RequirementState[] };
export type Resource = { id: string; title: string; kind: string; source_uri: string };
export type PathStep = {
  id: string;
  name: string;
  description: string;
  status: "satisfied" | "not_satisfied" | "unknown";
  reason: string;
  score: number | null;
  assessment: string | null;
  assessed_at: string | null;
  waived: boolean;
  ready: boolean;
  blocked_by: string[];
  prerequisites: string[];
  requirement_groups: RequirementGroup[];
  resources: Resource[];
};
export type LearningPath = { student: Student; steps: PathStep[]; mastered: number; total: number; threshold: number; as_of: string; notice: string };
export type Waiver = {
  id: string;
  skill_id: string;
  reason: string;
  granted_by: string;
  effective_from: string;
  effective_until: string | null;
  revoked_at: string | null;
  created_at: string;
};
export type Citation = { id: string; title: string; excerpt: string; source_version: string; illustrative: boolean };
export type Brief = { answer: string; ai_explanation: string | null; evidence: Citation[]; generator: string; warning: string | null; notice: string; request_id: string };

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
    credentials: "same-origin",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function homeFor(role: Role | string): string {
  if (role === "teacher") return "/teacher";
  if (role === "advisor") return "/advisor";
  return "/";
}

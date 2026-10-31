import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { components, paths } from "./schema";

export type S = components["schemas"];

export type ApiResponse<
  PathKey extends keyof paths,
  MethodKey extends keyof paths[PathKey],
> = paths[PathKey][MethodKey] extends {
  responses: { 200: { content: { "application/json": infer R } } };
}
  ? R
  : paths[PathKey][MethodKey] extends {
      responses: { 201: { content: { "application/json": infer R2 } } };
    }
    ? R2
    : unknown;

export type HealthResponse = S["HealthResponse"];
export type TemplateOut = S["TemplateOut"];
export type CreateProjectRequest = S["CreateProjectRequest"];
export type ProjectOut = S["ProjectOut"];
export type InputsRequest = S["InputsRequest"];
export type InputsOut = S["InputsOut"];
export type JobAccepted = S["JobAccepted"];
export type DeckResponse = S["DeckResponse"];
export type LayoutMapUpdate = S["LayoutMapUpdate"];

export type JobEvent = {
  seq: number;
  stage: string;
  status: "started" | "progress" | "done" | "error";
  message: string;
  version?: number | null;
};

export type VersionInfo = {
  version: number;
  created_by: string;
  summary: string;
  created_at: string;
};

export type EditRequest = {
  command: string;
  base_version: number;
  target_slide_id?: string | null;
};

export type DeckPatchRequest = {
  base_version: number;
  slide_id: string;
  target: "title" | "subtitle" | "bullet" | "layout_style";
  bullet_index?: number | null;
  text: string;
};

export class ApiError extends Error {
  code: string;
  override message: string;
  detail?: Record<string, unknown> | null;

  constructor(code: string, message: string, detail?: Record<string, unknown> | null) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.message = message;
    this.detail = detail;
  }
}

async function request<T>(
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const url = path.startsWith("/") ? path : `/${path}`;
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  const init: RequestInit = {
    method,
    headers,
  };

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (err) {
    throw new ApiError(
      "NETWORK",
      err instanceof Error ? err.message : "Network error occurred",
      null,
    );
  }

  if (!response.ok) {
    let errorData: {
      code?: string;
      message?: string;
      detail?: Record<string, unknown> | null;
    } | null = null;

    try {
      errorData = (await response.json()) as {
        code?: string;
        message?: string;
        detail?: Record<string, unknown> | null;
      };
    } catch {
      // Non-JSON response
    }

    if (
      errorData &&
      typeof errorData.code === "string" &&
      typeof errorData.message === "string"
    ) {
      throw new ApiError(errorData.code, errorData.message, errorData.detail ?? null);
    }

    throw new ApiError(
      "NETWORK",
      `HTTP ${response.status}: ${response.statusText}`,
      null,
    );
  }

  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------
// Typed CONTRACT endpoints
// ---------------------------------------------------------------------------

export async function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("GET", "/api/health");
}

export async function listTemplates(): Promise<TemplateOut[]> {
  return request<TemplateOut[]>("GET", "/api/templates");
}

export async function uploadTemplate(
  file: File,
  name?: string,
): Promise<TemplateOut> {
  const formData = new FormData();
  formData.append("file", file);
  if (name) {
    formData.append("name", name);
  }

  const response = await fetch("/api/templates", {
    method: "POST",
    headers: {
      Accept: "application/json",
    },
    body: formData,
  });

  if (!response.ok) {
    let errorData: {
      code?: string;
      message?: string;
      detail?: Record<string, unknown> | null;
    } | null = null;
    try {
      errorData = (await response.json()) as {
        code?: string;
        message?: string;
        detail?: Record<string, unknown> | null;
      };
    } catch {
      // Non-JSON
    }
    if (errorData?.code && errorData?.message) {
      throw new ApiError(errorData.code, errorData.message, errorData.detail ?? null);
    }
    throw new ApiError("NETWORK", `HTTP ${response.status}: ${response.statusText}`, null);
  }

  return (await response.json()) as TemplateOut;
}

export async function updateLayoutMap(
  templateId: string,
  layoutMap: LayoutMapUpdate,
): Promise<TemplateOut> {
  return request<TemplateOut>(
    "PUT",
    `/api/templates/${templateId}/layout-map`,
    layoutMap,
  );
}

export async function createProject(
  data: CreateProjectRequest,
): Promise<ProjectOut> {
  return request<ProjectOut>("POST", "/api/projects", data);
}

export async function putInputs(
  projectId: string,
  data: InputsRequest,
): Promise<InputsOut> {
  return request<InputsOut>("PUT", `/api/projects/${projectId}/inputs`, data);
}

export type GenerateRequest = {
  template_id?: string | null;
};

export async function startGenerate(
  projectId: string,
  data?: GenerateRequest,
): Promise<JobAccepted> {
  return request<JobAccepted>("POST", `/api/projects/${projectId}/generate`, data);
}

export async function startEdit(
  projectId: string,
  data: EditRequest,
): Promise<JobAccepted> {
  return request<JobAccepted>("POST", `/api/projects/${projectId}/edit`, data);
}

export async function getDeck(
  projectId: string,
): Promise<DeckResponse> {
  return request<DeckResponse>("GET", `/api/projects/${projectId}/deck`);
}

export async function getVersions(
  projectId: string,
): Promise<VersionInfo[]> {
  return request<VersionInfo[]>("GET", `/api/projects/${projectId}/versions`);
}

export async function undo(
  projectId: string,
): Promise<DeckResponse> {
  return request<DeckResponse>("POST", `/api/projects/${projectId}/undo`);
}

export async function patchDeck(
  projectId: string,
  data: DeckPatchRequest,
): Promise<DeckResponse> {
  return request<DeckResponse>("PATCH", `/api/projects/${projectId}/deck`, data);
}

// ---------------------------------------------------------------------------
// URL builders (relative /api/... strings)
// ---------------------------------------------------------------------------

export function slidePngUrl(projectId: string, n: number, k: number): string {
  return `/api/projects/${projectId}/v/${n}/slides/${k}.png`;
}

export function pptxUrl(projectId: string, n: number): string {
  return `/api/projects/${projectId}/v/${n}/deck.pptx`;
}

export function diagramUrl(projectId: string, n: number): string {
  return `/api/projects/${projectId}/v/${n}/diagram.png`;
}

// ---------------------------------------------------------------------------
// Storage helpers (hold ids only, never deck data)
// ---------------------------------------------------------------------------

const PROJECT_STORAGE_KEY = "deckagent.projectId";

export function getStoredProjectId(): string | null {
  try {
    return localStorage.getItem(PROJECT_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredProjectId(id: string | null): void {
  try {
    if (id) {
      localStorage.setItem(PROJECT_STORAGE_KEY, id);
    } else {
      localStorage.removeItem(PROJECT_STORAGE_KEY);
    }
  } catch {
    // Ignore storage errors in restricted contexts
  }
}

export function getStoredJobId(projectId: string): string | null {
  try {
    return sessionStorage.getItem(`deckagent.job.${projectId}`);
  } catch {
    return null;
  }
}

export function setStoredJobId(projectId: string, jobId: string | null): void {
  try {
    if (jobId) {
      sessionStorage.setItem(`deckagent.job.${projectId}`, jobId);
    } else {
      sessionStorage.removeItem(`deckagent.job.${projectId}`);
    }
  } catch {
    // Ignore storage errors in restricted contexts
  }
}

// ---------------------------------------------------------------------------
// SSE Job Events hook
// ---------------------------------------------------------------------------

export function useJobEvents(
  jobId: string | null,
  projectId?: string | null,
): {
  events: JobEvent[];
  terminal: boolean;
  error: string | null;
} {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [terminal, setTerminal] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!jobId) {
      setEvents([]);
      setTerminal(false);
      setError(null);
      return;
    }

    setEvents([]);
    setTerminal(false);
    setError(null);

    let isTerminated = false;
    const source = new EventSource(`/api/jobs/${jobId}/events`);

    source.onmessage = (event: MessageEvent) => {
      try {
        const parsed = JSON.parse(event.data as string) as JobEvent;
        setEvents((prev) => {
          if (prev.some((e) => e.seq === parsed.seq)) {
            return prev;
          }
          const next = [...prev, parsed];
          next.sort((a, b) => a.seq - b.seq);
          return next;
        });

        const isTerminal = parsed.stage === "done" || parsed.status === "error";
        if (isTerminal) {
          isTerminated = true;
          setTerminal(true);
          source.close();

          const targetPid = projectId ?? getStoredProjectId();
          if (targetPid) {
            void queryClient.invalidateQueries({ queryKey: ["deck", targetPid] });
            void queryClient.invalidateQueries({ queryKey: ["versions", targetPid] });
          } else {
            void queryClient.invalidateQueries({ queryKey: ["deck"] });
            void queryClient.invalidateQueries({ queryKey: ["versions"] });
          }

          if (parsed.status === "error") {
            setError(parsed.message || "Job failed");
          }
        }
      } catch (err) {
        console.error("Failed to parse SSE event", err);
      }
    };

    source.onerror = () => {
      if (!isTerminated) {
        source.close();
        setError("SSE connection closed or error");
      }
    };

    return () => {
      source.close();
    };
  }, [jobId, projectId, queryClient]);

  return { events, terminal, error };
}

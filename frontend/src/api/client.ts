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

export async function getHealth(): Promise<ApiResponse<"/api/health", "get">> {
  return request<ApiResponse<"/api/health", "get">>("GET", "/api/health");
}

// Endpoints to be added in Prompt 3.5:
// - uploadTemplate(file: File, name?: string): POST /api/templates
// - listTemplates(): GET /api/templates
// - updateLayoutMap(id: string, layoutMap: S["LayoutMapUpdate"]): PUT /api/templates/{id}/layout-map
// - createProject(req: S["CreateProjectRequest"]): POST /api/projects
// - updateInputs(id: string, req: S["InputsRequest"]): PUT /api/projects/{id}/inputs
// - generateDeck(id: string): POST /api/projects/{id}/generate
// - editDeck(id: string, req: S["EditRequest"]): POST /api/projects/{id}/edit
// - getDeck(id: string): GET /api/projects/{id}/deck
// - getVersions(id: string): GET /api/projects/{id}/versions
// - undoDeck(id: string): POST /api/projects/{id}/undo
// - patchDeck(id: string, req: S["DeckPatchRequest"]): PATCH /api/projects/{id}/deck
// - subscribeJobEvents(jobId: string, onEvent: (ev: S["JobEvent"]) => void): GET /api/jobs/{job_id}/events

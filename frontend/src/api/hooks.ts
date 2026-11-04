import { useMutation, useQuery } from "@tanstack/react-query";
import type {
  CreateProjectRequest,
  DeckPatchRequest,
  DeckResponse,
  EditRequest,
  InputsOut,
  InputsRequest,
  JobAccepted,
  ProjectOut,
  TemplateOut,
  VersionInfo,
} from "./client";
import {
  ApiError,
  createProject,
  getDeck,
  getStoredJobId,
  getStoredProjectId,
  getVersions,
  listTemplates,
  patchDeck,
  putInputs,
  setStoredJobId,
  setStoredProjectId,
  startEdit,
  startGenerate,
  undo,
  useJobEvents,
} from "./client";

export {
  getStoredProjectId,
  setStoredProjectId,
  getStoredJobId,
  setStoredJobId,
  useJobEvents,
};

export function useDeck(projectId: string | null | undefined) {
  return useQuery<DeckResponse | null, Error>({
    queryKey: ["deck", projectId],
    queryFn: async () => {
      if (!projectId) return null;
      try {
        return await getDeck(projectId);
      } catch (err) {
        if (err instanceof ApiError && err.code === "NOT_FOUND") {
          const msg = (err.message || "").toLowerCase();
          if (msg.includes("project") && msg.includes("not found")) {
            setStoredProjectId(null);
          }
          return null;
        }
        throw err;
      }
    },
    enabled: Boolean(projectId),
  });
}

export function useVersions(projectId: string | null | undefined) {
  return useQuery<VersionInfo[], Error>({
    queryKey: ["versions", projectId],
    queryFn: async () => {
      if (!projectId) return [];
      try {
        return await getVersions(projectId);
      } catch (err) {
        if (err instanceof ApiError && err.code === "NOT_FOUND") {
          return [];
        }
        throw err;
      }
    },
    enabled: Boolean(projectId),
  });
}

export function useTemplates() {
  return useQuery<TemplateOut[], Error>({
    queryKey: ["templates"],
    queryFn: () => listTemplates(),
  });
}

export function useCreateProject() {
  return useMutation<ProjectOut, Error, CreateProjectRequest>({
    mutationFn: (data: CreateProjectRequest) => createProject(data),
  });
}

export function usePutInputs() {
  return useMutation<
    InputsOut,
    Error,
    { projectId: string; data: InputsRequest }
  >({
    mutationFn: ({
      projectId,
      data,
    }: {
      projectId: string;
      data: InputsRequest;
    }) => putInputs(projectId, data),
  });
}

export function useStartGenerate() {
  return useMutation<JobAccepted, Error, string>({
    mutationFn: (projectId: string) => startGenerate(projectId),
  });
}

export function useStartEdit() {
  return useMutation<
    JobAccepted,
    Error,
    { projectId: string; data: EditRequest }
  >({
    mutationFn: ({
      projectId,
      data,
    }: {
      projectId: string;
      data: EditRequest;
    }) => startEdit(projectId, data),
  });
}

export function useUndo() {
  return useMutation<DeckResponse, Error, string>({
    mutationFn: (projectId: string) => undo(projectId),
  });
}

export function usePatchDeck() {
  return useMutation<
    DeckResponse,
    Error,
    { projectId: string; data: DeckPatchRequest }
  >({
    mutationFn: ({
      projectId,
      data,
    }: {
      projectId: string;
      data: DeckPatchRequest;
    }) => patchDeck(projectId, data),
  });
}

import { useEffect, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useDeck, useTemplates } from "../api/hooks";
import {
  ApiError,
  createProject,
  putInputs,
  setStoredJobId,
  setStoredProjectId,
  startGenerate,
  uploadTemplate,
} from "../api/client";
import type { CreateProjectRequest } from "../api/client";

interface InputsPaneProps {
  projectId: string | null;
  onProjectChange: (id: string | null) => void;
  onJobStarted: (jobId: string) => void;
  isJobRunning: boolean;
}

export function InputsPane({
  projectId,
  onProjectChange,
  onJobStarted,
  isJobRunning,
}: InputsPaneProps) {
  const queryClient = useQueryClient();
  const { data: templates, isLoading: loadingTemplates } = useTemplates();
  const { data: deckResp } = useDeck(projectId);

  const [repoRef, setRepoRef] = useState<string>("");
  const [pitchNotes, setPitchNotes] = useState<string>("");
  const [chatText, setChatText] = useState<string>("");
  const [templateId, setTemplateId] = useState<string>("");
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  useEffect(() => {
    if (deckResp?.deck?.template_id) {
      setTemplateId(deckResp.deck.template_id);
    }
  }, [deckResp?.deck?.template_id]);

  function handleNewProject() {
    setStoredProjectId(null);
    onProjectChange(null);
    setRepoRef("");
    setPitchNotes("");
    setChatText("");
    setTemplateId("");
    setErrorMessage(null);
    setUploadStatus(null);
  }

  async function handleFileUpload(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.name.toLowerCase().endsWith(".pptx")) {
      setErrorMessage("Uploaded template must be a .pptx file.");
      return;
    }

    setErrorMessage(null);
    setUploadStatus(`Inspecting and uploading ${file.name}...`);
    try {
      const uploaded = await uploadTemplate(file);
      await queryClient.invalidateQueries({ queryKey: ["templates"] });
      setTemplateId(uploaded.id);
      setUploadStatus(`Loaded: ${uploaded.name}`);
      setTimeout(() => setUploadStatus(null), 5000);
    } catch (err: unknown) {
      setUploadStatus(null);
      if (err instanceof ApiError) {
        setErrorMessage(`Template upload error: ${err.message}`);
      } else if (err instanceof Error) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage("Failed to upload presentation template.");
      }
    } finally {
      e.target.value = "";
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setErrorMessage(null);

    const cleanRef = repoRef.trim().replace(/[\\/]+$/, "");
    if (!cleanRef && !projectId) {
      setErrorMessage("Please enter a repository URL or local filesystem path.");
      return;
    }

    // Default template fallback: template is optional
    const effectiveTemplateId =
      templateId ||
      (templates && templates.length > 0
        ? templates[0].id
        : "2230629ce0fd47bfabc752ea90b200a5");

    setIsSubmitting(true);
    try {
      let currentProjectId = projectId;

      // Create new project if no active project, or if user entered a repo path
      if (!currentProjectId || cleanRef) {
        const segments = cleanRef.split(/[\\/]/).filter(Boolean);
        const name = segments[segments.length - 1] || "deck-project";

        const isUrl = /^(https?:\/\/|git@)/i.test(cleanRef);
        const createPayload: CreateProjectRequest = {
          name,
          template_id: effectiveTemplateId,
          ...(isUrl ? { repo_url: cleanRef } : { repo_path: cleanRef || "." }),
        };

        const project = await createProject(createPayload);
        currentProjectId = project.id;
        setStoredProjectId(currentProjectId);
        onProjectChange(currentProjectId);
      }

      await putInputs(currentProjectId, {
        pitch_notes: pitchNotes,
        chat_text: chatText,
        criteria: "",
      });

      const accepted = await startGenerate(currentProjectId, {
        template_id: effectiveTemplateId,
      });
      setStoredJobId(currentProjectId, accepted.job_id);
      onJobStarted(accepted.job_id);
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.code === "JOB_IN_PROGRESS") {
          setErrorMessage("A job is already running for this project. Please wait for it to complete.");
        } else if (err.code === "REPO_UNREACHABLE" || err.code === "TOOL_MISSING") {
          setErrorMessage(err.message);
        } else {
          setErrorMessage(`[${err.code}] ${err.message}`);
        }
      } else if (err instanceof Error) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage("An unexpected error occurred while starting the job.");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <aside className="w-full flex flex-col h-full bg-slate-900 border-r border-slate-800 p-4 overflow-y-auto">
      <div className="flex items-center justify-between pb-3 mb-4 border-b border-slate-800">
        <div>
          <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Project Inputs</h2>
          <p className="text-xs text-slate-400">
            {projectId ? `Project: ${projectId.slice(0, 8)}...` : "New Presentation"}
          </p>
        </div>
        {projectId && (
          <button
            type="button"
            onClick={handleNewProject}
            disabled={isJobRunning || isSubmitting}
            className="text-xs px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white border border-slate-700 transition disabled:opacity-50"
          >
            New project
          </button>
        )}
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4 flex-1">
        <div>
          <label htmlFor="repo-input" className="block text-xs font-medium text-slate-300 mb-1">
            Repository URL / Path <span className="text-rose-400">*</span>
          </label>
          <input
            id="repo-input"
            type="text"
            value={repoRef}
            onChange={(e) => setRepoRef(e.target.value)}
            disabled={isJobRunning || isSubmitting}
            placeholder="https://github.com/org/repo or local/path"
            className="w-full px-3 py-2 text-xs bg-slate-950 border border-slate-800 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-60 disabled:cursor-not-allowed"
          />
        </div>

        <div>
          <div className="flex items-center justify-between mb-1">
            <label htmlFor="template-select" className="block text-xs font-medium text-slate-300">
              Presentation Template <span className="text-slate-500 font-normal">(Optional)</span>
            </label>
            <label className="text-[11px] text-indigo-400 hover:text-indigo-300 cursor-pointer inline-flex items-center gap-1 transition">
              <span>+ Upload .pptx</span>
              <input
                type="file"
                accept=".pptx"
                onChange={handleFileUpload}
                disabled={isJobRunning || isSubmitting}
                className="hidden"
              />
            </label>
          </div>
          <select
            id="template-select"
            value={templateId}
            onChange={(e) => setTemplateId(e.target.value)}
            disabled={isJobRunning || isSubmitting || loadingTemplates}
            className="w-full px-3 py-2 text-xs bg-slate-950 border border-slate-800 rounded-lg text-white focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            <option value="">Default Template (Auto / Built-in)</option>
            {templates?.map((tmpl) => {
              const warningCount = tmpl.manifest?.warnings?.length ?? 0;
              const mode = tmpl.manifest?.mode ?? "layouts";
              const countDesc =
                mode === "fill"
                  ? `${Object.keys(tmpl.manifest?.slots ?? {}).length || tmpl.manifest?.profile?.slots?.length || 0} slots`
                  : `${Object.keys(tmpl.manifest?.layout_map ?? {}).length || 0} layouts`;
              return (
                <option key={tmpl.id} value={tmpl.id}>
                  {tmpl.name} ({mode} mode, {countDesc})
                  {warningCount > 0 ? ` [${warningCount} warnings]` : ""}
                </option>
              );
            })}
          </select>
          {uploadStatus && (
            <p className="text-[11px] text-emerald-400 mt-1 flex items-center gap-1">
              <span>&#10003;</span> {uploadStatus}
            </p>
          )}
        </div>

        <div>
          <label htmlFor="pitch-notes" className="block text-xs font-medium text-slate-300 mb-1">
            Pitch Notes (Voice / Dictation)
          </label>
          <textarea
            id="pitch-notes"
            rows={4}
            value={pitchNotes}
            onChange={(e) => setPitchNotes(e.target.value)}
            disabled={isJobRunning || isSubmitting}
            placeholder="Dictate or type product narrative, problem statement, key differentiators..."
            className="w-full px-3 py-2 text-xs bg-slate-950 border border-slate-800 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 resize-none disabled:opacity-60"
          />
        </div>

        <div>
          <label htmlFor="chat-text" className="block text-xs font-medium text-slate-300 mb-1">
            Paste an LLM chat
          </label>
          <textarea
            id="chat-text"
            rows={5}
            value={chatText}
            onChange={(e) => setChatText(e.target.value)}
            disabled={isJobRunning || isSubmitting}
            placeholder="Paste raw conversation export. Keys, tokens, and secrets are automatically redacted."
            className="w-full px-3 py-2 text-xs bg-slate-950 border border-slate-800 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 resize-none font-mono text-[11px] disabled:opacity-60"
          />
        </div>

        {errorMessage && (
          <div className="rounded-lg bg-red-950/60 border border-red-800/80 p-3 text-xs text-red-300">
            {errorMessage}
          </div>
        )}

        <div className="mt-auto pt-2">
          <button
            type="submit"
            disabled={isJobRunning || isSubmitting}
            className="w-full py-2.5 px-4 bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 disabled:bg-slate-800 disabled:text-slate-500 disabled:cursor-not-allowed text-white font-medium text-xs rounded-lg transition shadow-md flex items-center justify-center gap-2"
          >
            {isJobRunning || isSubmitting ? (
              <>
                <span className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                <span>Generating Deck...</span>
              </>
            ) : (
              <span>Generate Deck</span>
            )}
          </button>
        </div>
      </form>
    </aside>
  );
}

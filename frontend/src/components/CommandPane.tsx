import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  setStoredJobId,
  startEdit,
  useJobEvents,
} from "../api/client";
import type { JobEvent } from "../api/client";
import { useDeck } from "../api/hooks";

interface CommandPaneProps {
  jobId: string | null;
  projectId: string | null;
  events?: JobEvent[];
  terminal?: boolean;
  error?: string | null;
  selectedSlideId?: string | null;
  isJobRunning?: boolean;
  onJobStarted?: (newJobId: string) => void;
}

export function CommandPane({
  jobId,
  projectId,
  events: propEvents,
  terminal: propTerminal,
  error: propError,
  selectedSlideId,
  isJobRunning: propIsJobRunning,
  onJobStarted,
}: CommandPaneProps) {
  const queryClient = useQueryClient();
  const { data: deckResp, refetch: refetchDeck } = useDeck(projectId);
  const hasDeck = Boolean(deckResp && deckResp.deck);
  const currentVersion = deckResp?.version;

  const fallback = useJobEvents(propEvents !== undefined ? null : jobId, projectId);
  const events = propEvents !== undefined ? propEvents : fallback.events;
  const terminal = propTerminal !== undefined ? propTerminal : fallback.terminal;
  const error = propError !== undefined ? propError : fallback.error;

  const [command, setCommand] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const isRunning = Boolean(jobId && !terminal && !error);
  const isBusy = Boolean(propIsJobRunning || isRunning || isSubmitting);
  const isInputDisabled = !hasDeck || isBusy;

  const doneEvent = [...events].reverse().find((ev) => ev.stage === "done");

  function renderStatusIcon(status: JobEvent["status"]) {
    switch (status) {
      case "started":
        return <span className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />;
      case "progress":
        return <span className="w-2 h-2 rounded-full bg-amber-400" />;
      case "done":
        return <span className="w-2 h-2 rounded-full bg-emerald-400" />;
      case "error":
        return <span className="w-2 h-2 rounded-full bg-rose-500" />;
    }
  }

  async function handleSend() {
    if (!projectId || !currentVersion || isInputDisabled || !command.trim()) {
      return;
    }

    const commandText = command.trim();
    setIsSubmitting(true);
    setErrorMessage(null);

    try {
      const accepted = await startEdit(projectId, {
        command: commandText,
        base_version: currentVersion,
        target_slide_id: selectedSlideId ?? undefined,
        deck: deckResp?.deck ?? undefined,
      });

      // Clear the textarea only after a successful send
      setCommand("");

      // Stores the job_id (sessionStorage)
      setStoredJobId(projectId, accepted.job_id);

      // Subscribes via useJobEvents
      onJobStarted?.(accepted.job_id);
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.code === "STALE_VERSION") {
          setErrorMessage("The deck changed since you loaded it. Refreshing...");
          void refetchDeck();
          void queryClient.invalidateQueries({ queryKey: ["deck", projectId] });
          void queryClient.invalidateQueries({ queryKey: ["versions", projectId] });
        } else if (err.code === "JOB_IN_PROGRESS") {
          setErrorMessage("A job is already running");
        } else {
          setErrorMessage(err.message || "Failed to submit edit command");
        }
      } else if (err instanceof Error) {
        setErrorMessage(err.message);
      } else {
        setErrorMessage("An unexpected error occurred");
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!isInputDisabled && command.trim()) {
        void handleSend();
      }
    }
  }

  return (
    <aside className="w-full flex flex-col h-full bg-slate-900 border-l border-slate-800 p-4 overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between pb-3 mb-4 border-b border-slate-800 shrink-0">
        <div>
          <h2 className="text-sm font-semibold text-white uppercase tracking-wider">
            Pipeline Events
          </h2>
          <p className="text-xs text-slate-400">
            {jobId ? `Job: ${jobId.slice(0, 8)}...` : "Execution Log"}
          </p>
        </div>
        {jobId && (
          <span
            className={`px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold uppercase ${
              error
                ? "bg-rose-950 text-rose-300 border border-rose-800"
                : terminal
                ? "bg-emerald-950 text-emerald-300 border border-emerald-800"
                : "bg-blue-950 text-blue-300 border border-blue-800 animate-pulse"
            }`}
          >
            {error ? "error" : terminal ? "done" : "running"}
          </span>
        )}
      </div>

      {/* Edit Command Textarea Area */}
      <div className="mb-4 pb-4 border-b border-slate-800 shrink-0 flex flex-col gap-2">
        <label
          htmlFor="command-input"
          className="block text-xs font-medium text-slate-300"
        >
          Edit Command
        </label>
        <textarea
          id="command-input"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isInputDisabled}
          placeholder="Hold your Wispr key and speak"
          rows={3}
          className="w-full px-3 py-2 text-xs bg-slate-950 border border-slate-800 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 disabled:opacity-60 disabled:cursor-not-allowed resize-none"
        />
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-slate-500">
            Enter to send, Shift+Enter for newline
          </span>
          <button
            type="button"
            onClick={() => void handleSend()}
            disabled={isInputDisabled || !command.trim()}
            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 text-white transition disabled:opacity-50 disabled:cursor-not-allowed shadow-sm"
          >
            {isSubmitting ? "Sending..." : "Send"}
          </button>
        </div>
        {errorMessage && (
          <div className="p-2.5 rounded-lg bg-rose-950/70 border border-rose-800 text-xs text-rose-200">
            {errorMessage}
          </div>
        )}
      </div>

      {/* Done message outcome line (green if version, neutral if null) */}
      {terminal && !error && doneEvent && (
        <div className="mb-4 shrink-0">
          {doneEvent.version !== null && doneEvent.version !== undefined ? (
            <div className="p-3 rounded-lg bg-emerald-950/60 border border-emerald-500/80 text-emerald-300 text-xs flex items-start gap-2 shadow-sm">
              <span className="w-2 h-2 rounded-full bg-emerald-400 mt-1 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="font-medium text-emerald-200 break-words leading-relaxed">
                  {doneEvent.message}
                </p>
              </div>
            </div>
          ) : (
            <div className="p-3 rounded-lg bg-slate-900 border border-slate-700 text-slate-300 text-xs flex items-start gap-2 shadow-sm">
              <span className="w-2 h-2 rounded-full bg-slate-400 mt-1 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="font-normal text-slate-300 break-words leading-relaxed">
                  {doneEvent.message}
                </p>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Execution Log / Progress Events */}
      {!jobId && (
        <div className="flex flex-col items-center justify-center flex-1 text-center p-6 text-slate-500">
          <div className="w-10 h-10 rounded-full bg-slate-950 border border-slate-800 flex items-center justify-center text-xs font-mono mb-2">
            SSE
          </div>
          <p className="text-xs">
            {hasDeck
              ? "Type an edit command above or dictate with Wispr."
              : "No active job. Click Generate to start presentation synthesis."}
          </p>
        </div>
      )}

      {jobId && (
        <div className="flex flex-col gap-2.5 flex-1">
          {events.length === 0 && !terminal && !error && (
            <div className="flex items-center gap-2 text-xs text-slate-400 py-3">
              <span className="w-3.5 h-3.5 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
              <span>Connecting to event stream...</span>
            </div>
          )}

          <div className="flex flex-col gap-2">
            {events.map((ev: JobEvent, idx: number) => {
              const isError = ev.status === "error";
              const isDone = ev.stage === "done";
              const isDoneWithVersion =
                isDone && ev.version !== null && ev.version !== undefined;
              const isDoneWithoutVersion =
                isDone && (ev.version === null || ev.version === undefined);

              return (
                <div
                  key={ev.seq ?? idx}
                  className={`p-2.5 rounded-lg border text-xs flex items-start gap-2.5 transition ${
                    isError
                      ? "bg-rose-950/40 border-rose-800/80 text-rose-200"
                      : isDoneWithVersion
                      ? "bg-emerald-950/40 border-emerald-800/80 text-emerald-200"
                      : isDoneWithoutVersion
                      ? "bg-slate-950/80 border-slate-700 text-slate-300"
                      : "bg-slate-950/40 border-slate-800/60 text-slate-300"
                  }`}
                >
                  <div className="mt-1 shrink-0">{renderStatusIcon(ev.status)}</div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between gap-1 mb-0.5">
                      <span className="font-mono text-[11px] font-semibold text-indigo-400 uppercase">
                        {ev.stage}
                      </span>
                      {ev.version !== null && ev.version !== undefined && (
                        <span className="text-[10px] font-mono px-1.5 py-0.2 rounded bg-indigo-950 text-indigo-300 border border-indigo-900">
                          v{ev.version}
                        </span>
                      )}
                    </div>
                    <p className="break-words leading-relaxed text-[11px]">
                      {ev.message}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>

          {error && (
            <div className="mt-2 p-3 rounded-lg bg-rose-950/70 border border-rose-800 text-xs text-rose-200">
              <span className="font-semibold block mb-0.5">Execution Failed</span>
              <span>{error}</span>
            </div>
          )}

          {terminal && !error && (
            <div
              className={`mt-2 p-2.5 rounded-lg border text-xs flex items-center gap-2 ${
                doneEvent?.version !== null && doneEvent?.version !== undefined
                  ? "bg-emerald-950/50 border-emerald-500/80 text-emerald-300"
                  : "bg-slate-900 border-slate-700 text-slate-300"
              }`}
            >
              <span
                className={`w-2 h-2 rounded-full ${
                  doneEvent?.version !== null && doneEvent?.version !== undefined
                    ? "bg-emerald-400"
                    : "bg-slate-400"
                }`}
              />
              <span className="font-medium">
                {doneEvent?.message || "Pipeline execution complete."}
              </span>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

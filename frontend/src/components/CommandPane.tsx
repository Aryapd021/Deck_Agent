import { useJobEvents } from "../api/client";
import type { JobEvent } from "../api/client";

interface CommandPaneProps {
  jobId: string | null;
  projectId: string | null;
  events?: JobEvent[];
  terminal?: boolean;
  error?: string | null;
}

export function CommandPane({
  jobId,
  projectId,
  events: propEvents,
  terminal: propTerminal,
  error: propError,
}: CommandPaneProps) {
  const fallback = useJobEvents(propEvents !== undefined ? null : jobId, projectId);
  const events = propEvents !== undefined ? propEvents : fallback.events;
  const terminal = propTerminal !== undefined ? propTerminal : fallback.terminal;
  const error = propError !== undefined ? propError : fallback.error;

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

  return (
    <aside className="w-full flex flex-col h-full bg-slate-900 border-l border-slate-800 p-4 overflow-y-auto">
      <div className="flex items-center justify-between pb-3 mb-4 border-b border-slate-800">
        <div>
          <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Pipeline Events</h2>
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

      {!jobId && (
        <div className="flex flex-col items-center justify-center flex-1 text-center p-6 text-slate-500">
          <div className="w-10 h-10 rounded-full bg-slate-950 border border-slate-800 flex items-center justify-center text-xs font-mono mb-2">
            SSE
          </div>
          <p className="text-xs">No active job. Click Generate to start presentation synthesis.</p>
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
              const isDone = ev.status === "done";

              return (
                <div
                  key={ev.seq ?? idx}
                  className={`p-2.5 rounded-lg border text-xs flex items-start gap-2.5 transition ${
                    isError
                      ? "bg-rose-950/40 border-rose-800/80 text-rose-200"
                      : isDone
                      ? "bg-slate-950/80 border-slate-800 text-slate-200"
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
                    <p className="break-words text-slate-300 leading-relaxed text-[11px]">
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
            <div className="mt-2 p-2.5 rounded-lg bg-emerald-950/50 border border-emerald-800 text-xs text-emerald-300 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-400" />
              <span>Pipeline execution complete.</span>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

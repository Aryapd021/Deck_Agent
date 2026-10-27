import { useQuery } from "@tanstack/react-query";
import { getHealth, ApiError } from "./api/client";

function App() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
  });

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex items-center justify-center p-6">
      <div className="w-full max-w-md bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl space-y-6">
        <div className="flex items-center justify-between border-b border-slate-800 pb-4">
          <div>
            <h1 className="text-lg font-semibold text-white tracking-tight">DeckAgent Status</h1>
            <p className="text-xs text-slate-400">Backend System &amp; Dependencies</p>
          </div>
          {data && (
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-emerald-950/80 text-emerald-400 border border-emerald-800/60">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              {data.status}
            </span>
          )}
        </div>

        {isLoading && (
          <div className="flex flex-col items-center justify-center py-8 space-y-3 text-slate-400">
            <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
            <p className="text-sm">Connecting to backend...</p>
          </div>
        )}

        {isError && (
          <div className="rounded-lg bg-red-950/50 border border-red-900/60 p-4 text-sm text-red-300 space-y-1">
            <p className="font-medium text-red-200">Failed to connect to backend</p>
            <p className="text-xs text-red-400 break-words">
              {error instanceof ApiError ? `[${error.code}] ${error.message}` : error instanceof Error ? error.message : "Unknown error"}
            </p>
          </div>
        )}

        {data && (
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-2">
              <div className="bg-slate-950/60 border border-slate-800/80 rounded-lg p-3 text-center">
                <span className="block text-[11px] font-medium text-slate-400 uppercase tracking-wider mb-1">
                  dot
                </span>
                <span
                  className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${
                    data.dot
                      ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                      : "bg-amber-950 text-amber-400 border border-amber-800"
                  }`}
                >
                  {data.dot ? "ready" : "missing"}
                </span>
              </div>

              <div className="bg-slate-950/60 border border-slate-800/80 rounded-lg p-3 text-center">
                <span className="block text-[11px] font-medium text-slate-400 uppercase tracking-wider mb-1">
                  git
                </span>
                <span
                  className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${
                    data.git
                      ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                      : "bg-red-950 text-red-400 border border-red-800"
                  }`}
                >
                  {data.git ? "ready" : "missing"}
                </span>
              </div>

              <div className="bg-slate-950/60 border border-slate-800/80 rounded-lg p-3 text-center">
                <span className="block text-[11px] font-medium text-slate-400 uppercase tracking-wider mb-1">
                  soffice
                </span>
                <span
                  className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${
                    data.soffice
                      ? "bg-emerald-950 text-emerald-400 border border-emerald-800"
                      : "bg-slate-800 text-slate-400 border border-slate-700"
                  }`}
                >
                  {data.soffice ? "ready" : "none"}
                </span>
              </div>
            </div>

            <div className="space-y-2 pt-2 border-t border-slate-800/60 text-xs">
              <div className="flex items-center justify-between py-1 text-slate-300">
                <span className="text-slate-400">Preview Mode</span>
                <span className="font-mono bg-slate-800 px-2 py-0.5 rounded text-slate-200">
                  {data.preview_mode}
                </span>
              </div>
              <div className="flex items-center justify-between py-1 text-slate-300">
                <span className="text-slate-400">LLM Provider</span>
                <span className="font-mono bg-slate-800 px-2 py-0.5 rounded text-slate-200">
                  {data.llm_provider}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default App;


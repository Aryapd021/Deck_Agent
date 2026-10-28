import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../api/client";
import type { HealthResponse } from "../api/client";

export function HealthBanner() {
  const { data: health } = useQuery<HealthResponse, Error>({
    queryKey: ["health"],
    queryFn: getHealth,
    staleTime: 30000,
  });

  if (!health) {
    return null;
  }

  const missingCoreTools: string[] = [];
  if (!health.dot) missingCoreTools.push("Graphviz dot");
  if (!health.git) missingCoreTools.push("Git");

  if (missingCoreTools.length > 0) {
    return (
      <div className="bg-red-950/90 border-b border-red-800/80 px-4 py-2.5 text-red-200 text-xs sm:text-sm flex items-center justify-between shadow-sm">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-red-400 animate-pulse" />
          <span className="font-semibold text-red-100">Critical Tool Missing:</span>
          <span>
            {missingCoreTools.join(", ")} is not detected in PATH. Diagram generation or repository ingest will fail.
          </span>
        </div>
        <span className="text-red-400 font-mono text-xs hidden sm:inline">status: error</span>
      </div>
    );
  }

  const outlineFallback = !health.soffice || health.preview_mode === "outline";
  if (outlineFallback) {
    return (
      <div className="bg-slate-900/90 border-b border-slate-800 px-4 py-2 text-slate-300 text-xs flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
          <span className="font-medium text-slate-200">Outline Preview Mode:</span>
          <span className="text-slate-400">
            {!health.soffice
              ? "LibreOffice (soffice) not found in PATH."
              : "Preview mode set to outline."}{" "}
            Slide previews fall back to the outline view.
          </span>
        </div>
        <span className="text-slate-500 font-mono text-[11px] hidden sm:inline">preview: outline</span>
      </div>
    );
  }

  return null;
}

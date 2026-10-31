import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useDeck, useUndo, useVersions } from "../api/hooks";
import { ApiError, downloadPptx } from "../api/client";

interface VersionBarProps {
  projectId: string | null;
  isJobRunning?: boolean;
}

export function VersionBar({ projectId, isJobRunning }: VersionBarProps) {
  const queryClient = useQueryClient();
  const { data: versions = [] } = useVersions(projectId);
  const { data: deckResp } = useDeck(projectId);
  const undoMutation = useUndo();
  const [undoError, setUndoError] = useState<string | null>(null);
  const [isDownloading, setIsDownloading] = useState<boolean>(false);

  async function handleDownload() {
    if (!projectId || !currentVersion || isDownloading) return;
    setIsDownloading(true);
    try {
      await downloadPptx(projectId, currentVersion, deckResp?.deck);
    } catch (err) {
      console.error("Download failed:", err);
      alert("Failed to download presentation. Please try generating or editing again.");
    } finally {
      setIsDownloading(false);
    }
  }

  if (!projectId || versions.length === 0) {
    return null;
  }

  // Oldest to newest: v1, v2, v3...
  const sortedVersions = [...versions].sort((a, b) => a.version - b.version);

  // Current (highest) version
  const currentVersion =
    deckResp?.version ?? sortedVersions[sortedVersions.length - 1]?.version;

  // Latest summary line
  const latestSummary =
    deckResp?.summary ?? sortedVersions[sortedVersions.length - 1]?.summary;

  async function handleUndo() {
    if (
      !projectId ||
      versions.length < 2 ||
      isJobRunning ||
      undoMutation.isPending
    ) {
      return;
    }
    setUndoError(null);
    try {
      await undoMutation.mutateAsync(projectId);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["deck", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["versions", projectId] }),
      ]);
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        setUndoError(err.message);
      } else if (err instanceof Error) {
        setUndoError(err.message);
      } else {
        setUndoError("Failed to undo");
      }
    }
  }

  const isUndoDisabled =
    versions.length < 2 || Boolean(isJobRunning) || undoMutation.isPending;

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-sm space-y-3">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider mr-1">
            Versions:
          </span>
          {sortedVersions.map((v) => {
            const isCurrent = v.version === currentVersion;
            const tooltip = `Created by: ${v.created_by}\nSummary: ${v.summary}`;
            return (
              <span
                key={v.version}
                title={tooltip}
                className={`px-2.5 py-1 rounded-full text-xs font-mono font-medium border cursor-default select-none transition ${
                  isCurrent
                    ? "bg-indigo-600 text-white border-indigo-500 shadow-sm"
                    : "bg-slate-950 text-slate-300 border-slate-800 hover:border-slate-700"
                }`}
              >
                v{v.version}
              </span>
            );
          })}
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void handleUndo()}
            disabled={isUndoDisabled}
            className="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
          >
            <span>Undo</span>
          </button>

          {currentVersion ? (
            <button
              type="button"
              onClick={() => void handleDownload()}
              disabled={isDownloading}
              className="px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 disabled:opacity-50 text-white transition flex items-center gap-1.5 shadow-sm"
            >
              <span>{isDownloading ? "Downloading..." : "Download"}</span>
            </button>
          ) : (
            <span className="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 text-slate-500 cursor-not-allowed">
              Download
            </span>
          )}
        </div>
      </div>

      {latestSummary && (
        <div className="text-xs text-slate-400 pt-2 border-t border-slate-800/80 flex items-center gap-2">
          <span className="text-slate-500 font-medium shrink-0">Latest:</span>
          <span className="text-slate-300 italic truncate" title={latestSummary}>
            {latestSummary}
          </span>
        </div>
      )}

      {undoError && (
        <div className="text-xs text-rose-300 bg-rose-950/60 border border-rose-800/80 rounded-lg p-2">
          {undoError}
        </div>
      )}
    </div>
  );
}

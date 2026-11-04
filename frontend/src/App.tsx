import { useEffect, useState } from "react";
import { HealthBanner } from "./components/HealthBanner";
import { InputsPane } from "./components/InputsPane";
import { DeckPane } from "./components/DeckPane";
import { CommandPane } from "./components/CommandPane";
import {
  getStoredJobId,
  getStoredProjectId,
  setStoredProjectId,
  useJobEvents,
} from "./api/client";

export default function App() {
  const [projectId, setProjectId] = useState<string | null>(() => getStoredProjectId());
  const [jobId, setJobId] = useState<string | null>(() => {
    const pid = getStoredProjectId();
    return pid ? getStoredJobId(pid) : null;
  });
  const [selectedSlideId, setSelectedSlideId] = useState<string | null>(null);

  // Unified SSE event tracking in App
  const { events, terminal, error: jobError } = useJobEvents(jobId, projectId);
  const isJobRunning = Boolean(jobId && !terminal && !jobError);

  useEffect(() => {
    if (projectId) {
      const storedJob = getStoredJobId(projectId);
      setJobId(storedJob);
    } else {
      setJobId(null);
      setSelectedSlideId(null);
    }
  }, [projectId]);

  useEffect(() => {
    if (jobError && jobId) {
      const timer = setTimeout(() => {
        setJobId(null);
      }, 4000);
      return () => clearTimeout(timer);
    }
  }, [jobError, jobId]);

  function handleProjectChange(newId: string | null) {
    setStoredProjectId(newId);
    setProjectId(newId);
    if (!newId) {
      setJobId(null);
      setSelectedSlideId(null);
    }
  }

  function handleJobStarted(newJobId: string) {
    setJobId(newJobId);
  }

  return (
    <div className="flex flex-col h-screen w-screen bg-slate-950 text-slate-100 overflow-hidden font-sans">
      {/* Top Health Warnings / Info Banner */}
      <HealthBanner />

      {/* App Navigation Header */}
      <header className="h-12 border-b border-slate-800 bg-slate-900/90 px-6 flex items-center justify-between shrink-0 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="w-6 h-6 rounded-md bg-indigo-600 flex items-center justify-center font-bold text-xs text-white shadow">
            D
          </div>
          <div>
            <h1 className="text-sm font-semibold tracking-tight text-white flex items-center gap-2">
              DeckAgent
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
                v1.0-preview
              </span>
            </h1>
          </div>
        </div>

        <div className="flex items-center gap-3 text-xs text-slate-400">
          {projectId ? (
            <span className="font-mono bg-slate-950 px-2.5 py-1 rounded border border-slate-800 text-slate-300">
              Project: {projectId.slice(0, 8)}...
            </span>
          ) : (
            <span className="italic text-slate-500">No project selected</span>
          )}
        </div>
      </header>

      {/* Three Column Studio Layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Inputs Column */}
        <div className="w-80 shrink-0 h-full flex flex-col">
          <InputsPane
            projectId={projectId}
            onProjectChange={handleProjectChange}
            onJobStarted={handleJobStarted}
            isJobRunning={isJobRunning}
          />
        </div>

        {/* Center: Deck Column */}
        <div className="flex-1 min-w-0 h-full flex flex-col">
          <DeckPane
            projectId={projectId}
            selectedSlideId={selectedSlideId}
            onSelectSlide={(slideId) => setSelectedSlideId(slideId)}
            isJobRunning={isJobRunning}
          />
        </div>

        {/* Right: Command / Progress Column */}
        <div className="w-80 shrink-0 h-full flex flex-col">
          <CommandPane
            jobId={jobId}
            projectId={projectId}
            events={events}
            terminal={terminal}
            error={jobError}
            selectedSlideId={selectedSlideId}
            isJobRunning={isJobRunning}
            onJobStarted={handleJobStarted}
          />
        </div>
      </div>
    </div>
  );
}

import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useDeck, usePatchDeck, useTemplates } from "../api/hooks";
import { ApiError, diagramUrl, getHealth, pptxUrl, slidePngUrl } from "../api/client";
import type { HealthResponse, S } from "../api/client";
import { VersionBar } from "./VersionBar";

type DeckSlide = S["Slide"];
type DeckBullet = S["Field"];

interface DeckPaneProps {
  projectId: string | null;
  selectedSlideId: string | null;
  onSelectSlide: (slideId: string) => void;
  isJobRunning?: boolean;
}

interface NeedsInputBulletProps {
  projectId: string;
  version: number;
  slideId: string;
  bulletIndex: number;
  initialText: string;
  maxLength: number;
  isJobRunning?: boolean;
}

function NeedsInputBullet({
  projectId,
  version,
  slideId,
  bulletIndex,
  initialText,
  maxLength,
  isJobRunning,
}: NeedsInputBulletProps) {
  const [text, setText] = useState(initialText);
  const [prevInitial, setPrevInitial] = useState(initialText);
  if (prevInitial !== initialText) {
    setPrevInitial(initialText);
    setText(initialText);
  }
  const [isSaving, setIsSaving] = useState(false);
  const [staleError, setStaleError] = useState<string | null>(null);
  const isSavingRef = useRef(false);

  const queryClient = useQueryClient();
  const patchMutation = usePatchDeck();

  async function handleSave() {
    const trimmed = text.trim();
    if (!trimmed || trimmed === initialText || isSavingRef.current || isJobRunning) {
      return;
    }

    isSavingRef.current = true;
    setIsSaving(true);
    setStaleError(null);

    try {
      await patchMutation.mutateAsync({
        projectId,
        data: {
          base_version: version,
          slide_id: slideId,
          target: "bullet",
          bullet_index: bulletIndex,
          text: trimmed,
        },
      });

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["deck", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["versions", projectId] }),
      ]);
    } catch (err: unknown) {
      if (err instanceof ApiError && err.code === "STALE_VERSION") {
        setStaleError("The deck changed since you loaded it. Refreshing...");
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["deck", projectId] }),
          queryClient.invalidateQueries({ queryKey: ["versions", projectId] }),
        ]);
      } else if (err instanceof ApiError) {
        setStaleError(err.message);
      } else if (err instanceof Error) {
        setStaleError(err.message);
      } else {
        setStaleError("Failed to save changes");
      }
    } finally {
      setIsSaving(false);
      isSavingRef.current = false;
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      void handleSave();
    }
  }

  return (
    <div className="w-full flex flex-col gap-1" onClick={(e) => e.stopPropagation()}>
      <input
        type="text"
        value={text}
        maxLength={maxLength}
        disabled={Boolean(isJobRunning) || isSaving}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={() => void handleSave()}
        className="w-full px-2 py-1 text-xs bg-slate-900 border border-amber-600/60 rounded text-amber-100 placeholder-amber-400/50 focus:outline-none focus:ring-1 focus:ring-amber-500 disabled:opacity-60 disabled:cursor-not-allowed"
      />
      {staleError && (
        <span className="text-[11px] text-rose-400 font-medium">
          {staleError}
        </span>
      )}
    </div>
  );
}

export function DeckPane({
  projectId,
  selectedSlideId,
  onSelectSlide,
  isJobRunning,
}: DeckPaneProps) {
  const { data: deckResp, isLoading: loadingDeck } = useDeck(projectId);
  const { data: templates } = useTemplates();
  const { data: health } = useQuery<HealthResponse, Error>({
    queryKey: ["health"],
    queryFn: getHealth,
  });

  const [activeTab, setActiveTab] = useState<"slides" | "diagram">("slides");
  const [failedPreviews, setFailedPreviews] = useState<Record<number, boolean>>({});
  const [diagramImgFailed, setDiagramImgFailed] = useState<boolean>(false);
  const [updatingLayoutSlideId, setUpdatingLayoutSlideId] = useState<string | null>(null);

  const queryClient = useQueryClient();
  const patchMutation = usePatchDeck();

  const deck = deckResp?.deck;
  const version = deckResp?.version;

  async function handleSetLayoutStyle(slideId: string, style: "vertical" | "horizontal" | "auto") {
    if (!projectId || !version || isJobRunning || patchMutation.isPending) return;
    setUpdatingLayoutSlideId(slideId);
    try {
      await patchMutation.mutateAsync({
        projectId,
        data: {
          base_version: version,
          slide_id: slideId,
          target: "layout_style",
          text: style,
        },
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["deck", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["versions", projectId] }),
      ]);
    } catch (err) {
      console.error("Failed to update layout style:", err);
    } finally {
      setUpdatingLayoutSlideId(null);
    }
  }

  function renderSourceBadge(bullet: DeckBullet) {
    const src = bullet.source;
    if (src === "needs_input") {
      return (
        <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold bg-amber-900/80 text-amber-300 border border-amber-700/80 uppercase">
          needs_input
        </span>
      );
    }
    if (src === "repo") {
      return (
        <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold bg-cyan-950 text-cyan-400 border border-cyan-800 uppercase">
          repo
        </span>
      );
    }
    if (src === "voice") {
      return (
        <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold bg-purple-950 text-purple-400 border border-purple-800 uppercase">
          voice
        </span>
      );
    }
    if (src === "chat") {
      return (
        <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-950 text-emerald-400 border border-emerald-800 uppercase">
          chat
        </span>
      );
    }
    if (src === "user") {
      return (
        <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold bg-blue-950 text-blue-400 border border-blue-800 uppercase">
          you
        </span>
      );
    }
    return (
      <span className="inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold bg-slate-800 text-slate-400">
        {src}
      </span>
    );
  }

  return (
    <main className="flex-1 flex flex-col h-full bg-slate-950 overflow-hidden">
      {/* Header bar */}
      <div className="h-14 border-b border-slate-800 bg-slate-900/60 px-5 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-white tracking-wide">Presentation Deck</h2>
            {version ? (
              <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-indigo-950 text-indigo-300 border border-indigo-800">
                v{version}
              </span>
            ) : (
              <span className="text-xs text-slate-500">No version</span>
            )}
          </div>

          {deck && (
            <div className="flex items-center bg-slate-950 rounded-lg p-0.5 border border-slate-800 text-xs">
              <button
                type="button"
                onClick={() => setActiveTab("slides")}
                className={`px-3 py-1 rounded-md transition ${
                  activeTab === "slides"
                    ? "bg-slate-800 text-white font-medium"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                Slides ({deck.slides.length})
              </button>
              <button
                type="button"
                onClick={() => setActiveTab("diagram")}
                className={`px-3 py-1 rounded-md transition ${
                  activeTab === "diagram"
                    ? "bg-slate-800 text-white font-medium"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                Diagram
              </button>
            </div>
          )}
        </div>

        <div>
          {projectId && version ? (
            <a
              href={pptxUrl(projectId, version)}
              download={`deck_v${version}.pptx`}
              className="px-3 py-1.5 rounded-lg text-xs font-medium bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 text-white transition flex items-center gap-1.5 shadow-sm"
            >
              <span>Download .pptx</span>
            </a>
          ) : (
            <span className="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 text-slate-500 cursor-not-allowed">
              Download .pptx
            </span>
          )}
        </div>
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-y-auto p-5">
        {loadingDeck && (
          <div className="flex flex-col items-center justify-center h-64 text-slate-400 gap-3">
            <span className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
            <p className="text-sm">Loading presentation deck...</p>
          </div>
        )}

        {!loadingDeck && !deck && (
          <div className="flex flex-col items-center justify-center h-64 text-center border-2 border-dashed border-slate-800 rounded-xl p-8 max-w-md mx-auto my-12">
            <div className="w-12 h-12 rounded-full bg-slate-900 border border-slate-800 flex items-center justify-center text-slate-500 text-lg mb-3">
              PPTX
            </div>
            <h3 className="text-sm font-semibold text-slate-300 mb-1">No deck generated yet</h3>
            <p className="text-xs text-slate-500">
              Provide your repository ref and narrative notes in the left pane, then click Generate to produce a 10-slide deck.
            </p>
          </div>
        )}

        {!loadingDeck && deck && activeTab === "slides" && (
          <div className="flex flex-col gap-5 max-w-3xl mx-auto pb-8">
            <VersionBar projectId={projectId} isJobRunning={isJobRunning} />
            {deck.slides.map((slide: DeckSlide, index: number) => {
              const slideNum = index + 1;
              const isSelected = selectedSlideId === slide.id;
              const allowThumbnail =
                Boolean(health?.soffice) &&
                health?.preview_mode !== "outline" &&
                !failedPreviews[slideNum] &&
                Boolean(projectId) &&
                Boolean(version);

              return (
                <div
                  key={slide.id || index}
                  onClick={() => onSelectSlide(slide.id || "")}
                  className={`bg-slate-900 border rounded-xl p-4 transition cursor-pointer shadow-md ${
                    isSelected
                      ? "ring-2 ring-indigo-500 border-transparent bg-slate-900/90"
                      : "border-slate-800 hover:border-slate-700"
                  }`}
                >
                  {/* Thumbnail if enabled and available */}
                  {allowThumbnail && projectId && version && (
                    <div className="mb-3 rounded overflow-hidden border border-slate-800/80 bg-slate-950">
                      <img
                        src={slidePngUrl(projectId, version, slideNum)}
                        alt={`Slide ${slideNum}: ${slide.title.text}`}
                        onError={() =>
                          setFailedPreviews((prev) => ({ ...prev, [slideNum]: true }))
                        }
                        className="w-full h-auto object-contain max-h-56"
                      />
                    </div>
                  )}

                  {/* Outline view */}
                  <div className="space-y-3">
                    <div className="flex items-center justify-between gap-2 border-b border-slate-800/80 pb-2">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-bold text-slate-500">
                          #{slideNum}
                        </span>
                        <span className="px-2 py-0.5 rounded text-[11px] font-mono font-medium bg-slate-800 text-slate-300 border border-slate-700">
                          {slide.type}
                        </span>
                      </div>

                      <div className="flex items-center gap-2">
                        {slide.bullets && slide.bullets.length > 1 && (
                          <div
                            className="flex items-center bg-slate-950 rounded border border-slate-800 p-0.5 text-[10px]"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <button
                              type="button"
                              title="Stack bullet points one below the other (Vertical)"
                              disabled={Boolean(isJobRunning) || updatingLayoutSlideId === slide.id}
                              onClick={() => handleSetLayoutStyle(slide.id || "", "vertical")}
                              className={`px-2 py-0.5 rounded transition font-medium flex items-center gap-1 ${
                                slide.layout_style === "vertical"
                                  ? "bg-indigo-600 text-white shadow-sm"
                                  : "text-slate-400 hover:text-white"
                              }`}
                            >
                              <span>↓ Below</span>
                            </button>
                            <button
                              type="button"
                              title="Place bullet points one beside another (Horizontal)"
                              disabled={Boolean(isJobRunning) || updatingLayoutSlideId === slide.id}
                              onClick={() => handleSetLayoutStyle(slide.id || "", "horizontal")}
                              className={`px-2 py-0.5 rounded transition font-medium flex items-center gap-1 ${
                                slide.layout_style === "horizontal"
                                  ? "bg-indigo-600 text-white shadow-sm"
                                  : "text-slate-400 hover:text-white"
                              }`}
                            >
                              <span>→ Beside</span>
                            </button>
                            <button
                              type="button"
                              title="Automatic responsive layout"
                              disabled={Boolean(isJobRunning) || updatingLayoutSlideId === slide.id}
                              onClick={() => handleSetLayoutStyle(slide.id || "", "auto")}
                              className={`px-1.5 py-0.5 rounded transition ${
                                !slide.layout_style || slide.layout_style === "auto"
                                  ? "bg-slate-800 text-slate-200"
                                  : "text-slate-500 hover:text-slate-300"
                              }`}
                            >
                              Auto
                            </button>
                          </div>
                        )}
                        {slide.image_ref && (
                          <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-950/60 text-indigo-400 border border-indigo-900">
                            image: {slide.image_ref}
                          </span>
                        )}
                      </div>
                    </div>

                    <div>
                      <h4 className="text-sm font-semibold text-white tracking-tight">
                        {slide.title.text}
                      </h4>
                      {slide.subtitle?.text && (
                        <p className="text-xs text-slate-400 mt-0.5">
                          {slide.subtitle.text}
                        </p>
                      )}
                    </div>

                    {slide.bullets && slide.bullets.length > 0 && (
                      <div className="flex flex-col gap-1.5 pt-1">
                        {slide.bullets.map((bullet: DeckBullet, bIdx: number) => {
                          const isNeedsInput = bullet.source === "needs_input";
                          const currentTemplate = templates?.find((t) => t.id === deck?.template_id);
                          const slideTypeStr = typeof slide.type === "string" ? slide.type : String(slide.type);
                          const limits = currentTemplate?.manifest?.limits?.[slideTypeStr];
                          const bulletMax = limits?.bullet_max ?? 100;

                          return (
                            <div
                              key={bIdx}
                              className={`p-2 rounded-lg text-xs flex items-start justify-between gap-3 ${
                                isNeedsInput
                                  ? "bg-amber-950/40 border border-amber-800/60 text-amber-200"
                                  : "bg-slate-950/70 border border-slate-800/60 text-slate-200"
                              }`}
                            >
                              <div className="flex-1 min-w-0">
                                {isNeedsInput && projectId && version ? (
                                  <NeedsInputBullet
                                    projectId={projectId}
                                    version={version}
                                    slideId={slide.id || ""}
                                    bulletIndex={bIdx}
                                    initialText={bullet.text}
                                    maxLength={bulletMax}
                                    isJobRunning={isJobRunning}
                                  />
                                ) : (
                                  <>
                                    <span className="leading-relaxed">{bullet.text}</span>
                                    {bullet.source === "repo" && bullet.evidence && (
                                      <span className="block mt-0.5 text-[10px] font-mono text-cyan-400/80">
                                        evidence: {bullet.evidence}
                                      </span>
                                    )}
                                  </>
                                )}
                              </div>
                              <div className="shrink-0 mt-0.5">
                                {renderSourceBadge(bullet)}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {!loadingDeck && deck && activeTab === "diagram" && (
          <div className="max-w-2xl mx-auto py-4 space-y-6">
            <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-lg">
              <h3 className="text-sm font-semibold text-white mb-3">Architecture Diagram</h3>
              {projectId && version && !diagramImgFailed ? (
                <div className="rounded-lg bg-slate-950 border border-slate-800 p-3 flex justify-center">
                  <img
                    src={diagramUrl(projectId, version)}
                    alt="System Architecture Diagram"
                    onError={() => setDiagramImgFailed(true)}
                    className="max-w-full h-auto rounded shadow"
                  />
                </div>
              ) : (
                <div className="text-xs text-slate-400">
                  {deck.diagram ? (
                    <div className="space-y-4">
                      <p className="text-slate-500 italic">
                        Diagram image rendering unavailable; showing graph components:
                      </p>
                      <div>
                        <h4 className="font-semibold text-slate-300 mb-1.5">Nodes:</h4>
                        <div className="grid grid-cols-2 gap-2">
                          {deck.diagram.nodes.map((node) => (
                            <div
                              key={node.id}
                              className="bg-slate-950 border border-slate-800 p-2 rounded text-xs"
                            >
                              <span className="font-medium text-white">{node.label}</span>
                              <span className="ml-2 text-[10px] font-mono text-indigo-400">
                                [{node.kind}]
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                      {deck.diagram.edges && deck.diagram.edges.length > 0 && (
                        <div>
                          <h4 className="font-semibold text-slate-300 mb-1.5">Edges:</h4>
                          <ul className="space-y-1 font-mono text-xs text-slate-400">
                            {deck.diagram.edges.map((edge, idx) => (
                              <li key={idx} className="bg-slate-950 p-1.5 rounded border border-slate-800">
                                {edge.from} &rarr; {edge.to}
                                {edge.label ? ` (${edge.label})` : ""}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  ) : (
                    <p>No architecture diagram associated with this deck.</p>
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}

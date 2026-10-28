import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useDeck } from "../api/hooks";
import { diagramUrl, getHealth, pptxUrl, slidePngUrl } from "../api/client";
import type { HealthResponse, S } from "../api/client";

type DeckSlide = S["Slide"];
type DeckBullet = S["Field"];

interface DeckPaneProps {
  projectId: string | null;
  selectedSlideId: string | null;
  onSelectSlide: (slideId: string) => void;
}

export function DeckPane({
  projectId,
  selectedSlideId,
  onSelectSlide,
}: DeckPaneProps) {
  const { data: deckResp, isLoading: loadingDeck } = useDeck(projectId);
  const { data: health } = useQuery<HealthResponse, Error>({
    queryKey: ["health"],
    queryFn: getHealth,
  });

  const [activeTab, setActiveTab] = useState<"slides" | "diagram">("slides");
  const [failedPreviews, setFailedPreviews] = useState<Record<number, boolean>>({});
  const [diagramImgFailed, setDiagramImgFailed] = useState<boolean>(false);

  const deck = deckResp?.deck;
  const version = deckResp?.version;

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
                      {slide.image_ref && (
                        <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-indigo-950/60 text-indigo-400 border border-indigo-900">
                          image: {slide.image_ref}
                        </span>
                      )}
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
                          return (
                            <div
                              key={bIdx}
                              className={`p-2 rounded-lg text-xs flex items-start justify-between gap-3 ${
                                isNeedsInput
                                  ? "bg-amber-950/40 border border-amber-800/60 text-amber-200"
                                  : "bg-slate-950/70 border border-slate-800/60 text-slate-200"
                              }`}
                            >
                              <div className="flex-1">
                                <span className="leading-relaxed">{bullet.text}</span>
                                {bullet.source === "repo" && bullet.evidence && (
                                  <span className="block mt-0.5 text-[10px] font-mono text-cyan-400/80">
                                    evidence: {bullet.evidence}
                                  </span>
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

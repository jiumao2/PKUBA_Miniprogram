import { useEffect, useRef, useState } from 'react';
import type {
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
} from 'react';
import { Blend, FileText, Minus, Plus } from 'lucide-react';
import type { ScoresheetDocument, TemplateDefinition } from '../types';
import { PdfPage } from './PdfPage';
import { SceneOverlay } from './SceneOverlay';

interface DocumentCanvasProps {
  document: ScoresheetDocument | null;
  definition: TemplateDefinition;
  selectedField: string;
  onSelect: (field: string) => void;
  anomalyFields?: ReadonlySet<string>;
}

export function DocumentCanvas({
  document,
  definition,
  selectedField,
  onSelect,
  anomalyFields,
}: DocumentCanvasProps) {
  const [zoom, setZoom] = useState(0.9);
  const zoomRef = useRef(0.9);
  const pageScroll = useRef<HTMLDivElement>(null);
  const pageStage = useRef<HTMLDivElement>(null);
  const panSession = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    scrollLeft: number;
    scrollTop: number;
    moved: boolean;
  } | null>(null);
  const suppressClick = useRef(false);
  const [panning, setPanning] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const [sourceOpacity, setSourceOpacity] = useState(0.58);
  const sourceUrl = document?.source.original_url || document?.source.aligned_url || '';

  const zoomAt = (value: number, clientX: number, clientY: number) => {
    const scroller = pageScroll.current;
    const stage = pageStage.current;
    if (!scroller || !stage) return;
    const nextZoom = Math.min(1.8, Math.max(0.55, Math.round(value * 100) / 100));
    if (nextZoom === zoomRef.current) return;
    const stageBefore = stage.getBoundingClientRect();
    const anchorX = Math.min(1, Math.max(0, (clientX - stageBefore.left) / stageBefore.width));
    const anchorY = Math.min(1, Math.max(0, (clientY - stageBefore.top) / stageBefore.height));
    zoomRef.current = nextZoom;
    setZoom(nextZoom);
    window.requestAnimationFrame(() => {
      const stageAfter = pageStage.current?.getBoundingClientRect();
      if (!stageAfter) return;
      scroller.scrollLeft += stageAfter.left + stageAfter.width * anchorX - clientX;
      scroller.scrollTop += stageAfter.top + stageAfter.height * anchorY - clientY;
    });
  };

  const zoomFromCenter = (nextZoom: number) => {
    const rect = pageScroll.current?.getBoundingClientRect();
    if (!rect) return;
    zoomAt(nextZoom, rect.left + rect.width / 2, rect.top + rect.height / 2);
  };

  useEffect(() => {
    const scroller = pageScroll.current;
    if (!scroller) return;
    const handleWheel = (event: WheelEvent) => {
      if (!pageStage.current || event.deltaY === 0) return;
      event.preventDefault();
      const factor = event.deltaY < 0 ? 1.1 : 0.9;
      zoomAt(zoomRef.current * factor, event.clientX, event.clientY);
    };
    scroller.addEventListener('wheel', handleWheel, { passive: false });
    return () => scroller.removeEventListener('wheel', handleWheel);
  }, []);

  const beginPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const scroller = pageScroll.current;
    if (!scroller || event.button !== 0) return;
    panSession.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: scroller.scrollLeft,
      scrollTop: scroller.scrollTop,
      moved: false,
    };
  };

  const movePan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = panSession.current;
    const scroller = pageScroll.current;
    if (!session || !scroller || session.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - session.startX;
    const deltaY = event.clientY - session.startY;
    if (!session.moved && Math.hypot(deltaX, deltaY) < 4) return;
    if (!session.moved) {
      session.moved = true;
      event.currentTarget.setPointerCapture(event.pointerId);
      setPanning(true);
    }
    event.preventDefault();
    scroller.scrollLeft = session.scrollLeft - deltaX;
    scroller.scrollTop = session.scrollTop - deltaY;
  };

  const finishPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = panSession.current;
    if (!session || session.pointerId !== event.pointerId) return;
    panSession.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setPanning(false);
    if (session.moved) {
      suppressClick.current = true;
      window.setTimeout(() => { suppressClick.current = false; }, 0);
    }
  };

  const blockClickAfterPan = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (!suppressClick.current) return;
    suppressClick.current = false;
    event.preventDefault();
    event.stopPropagation();
  };

  return (
    <section className="workspace-pane document-pane" aria-label="标准记录表">
      <header className="pane-toolbar">
        <div>
          <span className="pane-kicker">重建结果</span>
          <div className="canvas-title-row">
            <strong>{document ? '标准记录表' : '空白标准记录表'}</strong>
            <span className="interaction-hint">{document ? '单击选区 · 双击编辑格 · 拖动平移 · 滚轮缩放' : '选择比赛并上传照片后开始填写 · 拖动平移 · 滚轮缩放'}</span>
          </div>
        </div>
        <div className="toolbar-cluster">
          {sourceUrl ? (
            <button
              className={showSource ? 'icon-button is-active' : 'icon-button'}
              onClick={() => setShowSource((value) => !value)}
              title="切换原图叠加"
              aria-label="切换原图叠加"
            >
              <Blend size={16} />
            </button>
          ) : null}
          {showSource && sourceUrl ? (
            <label className="inline-opacity" title="调整叠加原图透明度">
              <FileText size={13} />
              <span>原图</span>
              <input
                aria-label="原图透明度"
                type="range"
                min="0.15"
                max="0.9"
                step="0.05"
                value={sourceOpacity}
                onChange={(event) => setSourceOpacity(Number(event.target.value))}
              />
            </label>
          ) : null}
          <button className="icon-button" onClick={() => zoomFromCenter(zoom - 0.1)} aria-label="缩小">
            <Minus size={16} />
          </button>
          <span className="zoom-value">{Math.round(zoom * 100)}%</span>
          <button className="icon-button" onClick={() => zoomFromCenter(zoom + 0.1)} aria-label="放大">
            <Plus size={16} />
          </button>
        </div>
      </header>
      <div
        ref={pageScroll}
        className={`page-scroll${panning ? ' is-panning' : ''}`}
        aria-label="标准记录表画布：拖动平移，滚轮缩放"
        onPointerDown={beginPan}
        onPointerMove={movePan}
        onPointerUp={finishPan}
        onPointerCancel={finishPan}
        onClickCapture={blockClickAfterPan}
      >
        <div
          ref={pageStage}
          className="page-stage"
          style={{
            width: `${definition.page.width * zoom}px`,
            aspectRatio: `${definition.page.width} / ${definition.page.height}`,
          }}
        >
          <PdfPage />
          {showSource && sourceUrl ? (
            <img
              className="aligned-source-overlay"
              src={`${sourceUrl}${sourceUrl.includes('?') ? '&' : '?'}v=${document?.revision ?? 0}`}
              alt="原始记录表叠加"
              style={{ opacity: sourceOpacity }}
            />
          ) : null}
          {document ? (
            <SceneOverlay
              document={document}
              definition={definition}
              selectedField={selectedField}
              onSelect={onSelect}
              anomalyFields={anomalyFields}
            />
          ) : null}
        </div>
      </div>
    </section>
  );
}

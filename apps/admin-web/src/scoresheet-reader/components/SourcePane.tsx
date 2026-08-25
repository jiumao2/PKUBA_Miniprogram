import { useEffect, useMemo, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { ImagePlus, Minus, Plus, RefreshCw, ScanLine } from 'lucide-react';
import type { ScoresheetDocument } from '../types';

interface SourcePaneProps {
  document: ScoresheetDocument | null;
  onRequestUpload: () => void;
  onReloadSource?: () => Promise<void>;
  allowUpload?: boolean;
}

export function SourcePane({
  document,
  onRequestUpload,
  onReloadSource = async () => {},
  allowUpload = true,
}: SourcePaneProps) {
  const imageFrame = useRef<HTMLDivElement>(null);
  const sourceScroll = useRef<HTMLDivElement>(null);
  const activeSource = useRef('');
  const panSession = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    scrollLeft: number;
    scrollTop: number;
  } | null>(null);
  const zoomRef = useRef(1);
  const [zoom, setZoom] = useState(1);
  const [panning, setPanning] = useState(false);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [loadState, setLoadState] = useState<'loading' | 'loaded' | 'error'>('loaded');
  const sourceUrl = document?.source.original_url || document?.source.aligned_url || '';
  const sourceKey = `${document?.id ?? 'blank'}:${document?.source.version ?? 0}`;
  const filename = document?.source.original_filename || '尚未上传照片';
  const dimensions = document?.source.width && document?.source.height
    ? `${document.source.width} × ${document.source.height}px`
    : '';

  const imageUrl = useMemo(
    () => (sourceUrl
      ? `${sourceUrl}${sourceUrl.includes('?') ? '&' : '?'}v=${document?.revision ?? 0}-${reloadVersion}`
      : ''),
    [sourceUrl, document?.revision, reloadVersion],
  );

  const zoomAt = (value: number, clientX: number, clientY: number) => {
    const frame = imageFrame.current;
    const scroller = sourceScroll.current;
    if (!frame || !scroller) return;
    const nextZoom = Math.min(2.5, Math.max(0.65, Math.round(value * 100) / 100));
    if (nextZoom === zoomRef.current) return;
    const frameBefore = frame.getBoundingClientRect();
    const anchorX = Math.min(1, Math.max(0, (clientX - frameBefore.left) / frameBefore.width));
    const anchorY = Math.min(1, Math.max(0, (clientY - frameBefore.top) / frameBefore.height));
    zoomRef.current = nextZoom;
    setZoom(nextZoom);
    window.requestAnimationFrame(() => {
      const frameAfter = imageFrame.current?.getBoundingClientRect();
      if (!frameAfter) return;
      scroller.scrollLeft += frameAfter.left + frameAfter.width * anchorX - clientX;
      scroller.scrollTop += frameAfter.top + frameAfter.height * anchorY - clientY;
    });
  };

  const resetView = () => {
    zoomRef.current = 1;
    setZoom(1);
    window.requestAnimationFrame(() => {
      if (!sourceScroll.current) return;
      sourceScroll.current.scrollLeft = 0;
      sourceScroll.current.scrollTop = 0;
    });
  };

  useEffect(() => {
    if (activeSource.current === sourceKey) return;
    activeSource.current = sourceKey;
    setReloadVersion(0);
    setLoadState(sourceUrl ? 'loading' : 'loaded');
    resetView();
  }, [sourceKey, sourceUrl]);

  useEffect(() => {
    const scroller = sourceScroll.current;
    if (!scroller) return;
    const handleWheel = (event: WheelEvent) => {
      if (!imageFrame.current || event.deltaY === 0) return;
      event.preventDefault();
      const factor = event.deltaY < 0 ? 1.1 : 0.9;
      zoomAt(zoomRef.current * factor, event.clientX, event.clientY);
    };
    scroller.addEventListener('wheel', handleWheel, { passive: false });
    return () => scroller.removeEventListener('wheel', handleWheel);
  }, []);

  const beginPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const scroller = sourceScroll.current;
    if (!sourceUrl || !scroller || event.button !== 0) return;
    event.preventDefault();
    panSession.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: scroller.scrollLeft,
      scrollTop: scroller.scrollTop,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setPanning(true);
  };

  const movePan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = panSession.current;
    const scroller = sourceScroll.current;
    if (!session || !scroller || session.pointerId !== event.pointerId) return;
    scroller.scrollLeft = session.scrollLeft - (event.clientX - session.startX);
    scroller.scrollTop = session.scrollTop - (event.clientY - session.startY);
  };

  const finishPan = (event: ReactPointerEvent<HTMLDivElement>) => {
    const session = panSession.current;
    if (!session || session.pointerId !== event.pointerId) return;
    panSession.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setPanning(false);
  };

  const reloadImage = async () => {
    setLoadState('loading');
    await onReloadSource();
    setReloadVersion((value) => value + 1);
  };

  const zoomFromCenter = (nextZoom: number) => {
    const rect = sourceScroll.current?.getBoundingClientRect();
    if (!rect) return;
    zoomAt(nextZoom, rect.left + rect.width / 2, rect.top + rect.height / 2);
  };

  return (
    <section className="workspace-pane source-pane" aria-label="原始照片">
      <header className="source-header">
        <div className="source-heading">
          <span className="pane-kicker">照片对照台</span>
          <strong title={[filename, dimensions].filter(Boolean).join(' · ')}>{filename}</strong>
        </div>
        <div className="source-header-actions" aria-label="照片查看工具">
          <button className="source-icon-button" disabled={!sourceUrl || zoom <= 0.65} onClick={() => zoomFromCenter(zoom - 0.1)} title="缩小原图" aria-label="缩小原图">
            <Minus size={14} />
          </button>
          <button className="source-zoom-readout" disabled={!sourceUrl} onClick={resetView} title="恢复 100% 和初始位置" aria-label="原图倍率复位">
            {Math.round(zoom * 100)}%
          </button>
          <button className="source-icon-button" disabled={!sourceUrl || zoom >= 2.5} onClick={() => zoomFromCenter(zoom + 0.1)} title="放大原图" aria-label="放大原图">
            <Plus size={14} />
          </button>
          {allowUpload ? (
            <button className="source-icon-button" onClick={onRequestUpload} title={document ? '重新上传记录表照片' : '选择比赛并上传照片'} aria-label={document ? '重新上传照片' : '选择比赛并上传照片'}>
              <ImagePlus size={15} />
            </button>
          ) : null}
          <button className="source-icon-button" disabled={!sourceUrl} onClick={() => void reloadImage()} title="获取新链接并重新载入原图" aria-label="重新载入原图">
            <RefreshCw size={15} />
          </button>
        </div>
      </header>

      <div
        ref={sourceScroll}
        className={`source-scroll${panning ? ' is-panning' : ''}`}
        aria-label="照片画布：拖动平移，滚轮缩放"
        onPointerDown={beginPan}
        onPointerMove={movePan}
        onPointerUp={finishPan}
        onPointerCancel={finishPan}
      >
        {sourceUrl ? (
          <div className="source-canvas">
            <div className="source-image-stage" style={{ width: `${zoom * 100}%` }}>
              <div className={`source-image-frame${loadState === 'loading' ? ' is-loading' : ''}`} ref={imageFrame}>
                <img
                  src={imageUrl}
                  alt="上传的篮球记录表"
                  draggable={false}
                  onLoad={() => setLoadState('loaded')}
                  onError={() => setLoadState('error')}
                />
                {loadState === 'error' ? (
                  <div className="source-load-error">
                    <ScanLine size={24} />
                    <strong>照片载入失败</strong>
                    <button onClick={() => void reloadImage()}>重新载入原图</button>
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        ) : allowUpload ? (
          <button type="button" className="source-empty" onClick={onRequestUpload} aria-label="导入记录表照片">
            <span className="source-empty-mark"><ScanLine size={31} /></span>
            <strong>点击导入记录表照片</strong>
            <p>先选择比赛，再上传 JPEG、PNG 或 WebP 图片；上传后将自动开始识别。</p>
            <span className="source-empty-action">选择比赛并上传</span>
          </button>
        ) : (
          <div className="source-empty" aria-label="没有可查看的记录表原图">
            <span className="source-empty-mark"><ScanLine size={31} /></span>
            <strong>没有可查看的记录表原图</strong>
            <p>归档纠错只允许修改已有的结构化记录，不允许上传或替换照片。</p>
          </div>
        )}
      </div>
    </section>
  );
}

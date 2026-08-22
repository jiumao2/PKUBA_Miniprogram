import { useRef } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';

interface PaneResizerProps {
  label: string;
  value: number;
  active: boolean;
  onDragStart: (clientX: number) => void;
  onDrag: (clientX: number) => void;
  onDragEnd: () => void;
  onKeyboardMove: (direction: -1 | 1) => void;
  onReset: () => void;
}

export function PaneResizer({
  label,
  value,
  active,
  onDragStart,
  onDrag,
  onDragEnd,
  onKeyboardMove,
  onReset,
}: PaneResizerProps) {
  const pointerId = useRef<number | null>(null);

  const finishDrag = (event: PointerEvent<HTMLButtonElement>) => {
    if (pointerId.current !== event.pointerId) return;
    pointerId.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    onDragEnd();
  };

  const handleKeyboard = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    onKeyboardMove(event.key === 'ArrowLeft' ? -1 : 1);
  };

  return (
    <button
      type="button"
      className={active ? 'workspace-resizer is-active' : 'workspace-resizer'}
      role="separator"
      aria-label={label}
      aria-orientation="vertical"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value * 100)}
      title="拖动调整宽度；双击恢复默认比例"
      onDoubleClick={onReset}
      onKeyDown={handleKeyboard}
      onPointerDown={(event) => {
        event.preventDefault();
        pointerId.current = event.pointerId;
        event.currentTarget.setPointerCapture(event.pointerId);
        onDragStart(event.clientX);
      }}
      onPointerMove={(event) => {
        if (pointerId.current === event.pointerId) onDrag(event.clientX);
      }}
      onPointerUp={finishDrag}
      onPointerCancel={finishDrag}
    >
      <span aria-hidden="true" />
    </button>
  );
}

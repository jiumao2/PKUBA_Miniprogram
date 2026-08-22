import { useEffect, useRef, useState } from 'react';
import { GlobalWorkerOptions, getDocument } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

GlobalWorkerOptions.workerSrc = workerUrl;

export function PdfPage() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const pdf = await getDocument({ url: '/api/v1/scoresheets/template/pdf' }).promise;
        const page = await pdf.getPage(1);
        const viewport = page.getViewport({ scale: 2 });
        const canvas = canvasRef.current;
        if (!canvas || cancelled) return;
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        const context = canvas.getContext('2d');
        if (!context) return;
        await page.render({ canvas, canvasContext: context, viewport }).promise;
      } catch (loadError) {
        if (!cancelled) setError(loadError instanceof Error ? loadError.message : '模板加载失败');
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="pdf-page" aria-label="PDF 模板背景">
      {error ? <div className="pdf-error">{error}</div> : null}
      <canvas ref={canvasRef} />
    </div>
  );
}

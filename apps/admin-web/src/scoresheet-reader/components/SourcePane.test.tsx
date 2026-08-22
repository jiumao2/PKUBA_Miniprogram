import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { makeDocument } from '../test/fixtures';
import { SourcePane } from './SourcePane';

describe('source photo pane', () => {
  it('opens the game workflow when the empty pane is clicked', async () => {
    const user = userEvent.setup();
    const onRequestUpload = vi.fn();
    render(<SourcePane document={null} onRequestUpload={onRequestUpload} />);

    await user.click(screen.getByRole('button', { name: '导入记录表照片' }));
    expect(onRequestUpload).toHaveBeenCalledOnce();
    expect(screen.queryByLabelText(/撤回照片视图/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/照片视图向前/)).not.toBeInTheDocument();
  });

  it('zooms by controls and wheel, resets from the percentage, and reloads the image', async () => {
    const user = userEvent.setup();
    const document = makeDocument('real-document');
    document.source = {
      ...document.source,
      original_filename: 'record.jpg',
      original_url: '/record.jpg',
      width: 2400,
      height: 3200,
    };
    const onReloadSource = vi.fn().mockResolvedValue(undefined);
    render(
      <SourcePane
        document={document}
        onReloadSource={onReloadSource}
        onRequestUpload={vi.fn()}
      />,
    );

    const title = screen.getByText('record.jpg');
    expect(title).toHaveAttribute('title', 'record.jpg · 2400 × 3200px');
    await user.click(screen.getByRole('button', { name: '放大原图' }));
    expect(screen.getByRole('button', { name: '原图倍率复位' })).toHaveTextContent('110%');
    await user.click(screen.getByRole('button', { name: '原图倍率复位' }));
    expect(screen.getByRole('button', { name: '原图倍率复位' })).toHaveTextContent('100%');

    fireEvent.wheel(screen.getByLabelText('照片画布：拖动平移，滚轮缩放'), { deltaY: -100, clientX: 20, clientY: 20 });
    expect(screen.getByRole('button', { name: '原图倍率复位' })).toHaveTextContent('110%');
    const before = screen.getByRole('img', { name: '上传的篮球记录表' }).getAttribute('src');
    await user.click(screen.getByRole('button', { name: '重新载入原图' }));
    expect(onReloadSource).toHaveBeenCalledOnce();
    expect(screen.getByRole('img', { name: '上传的篮球记录表' }).getAttribute('src')).not.toBe(before);
  });
});

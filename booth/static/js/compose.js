// Canvas port of booth/compose.py: same canvas sizes and photo windows, so a
// demo-mode strip looks like the one the Python service prints.
export const LAYOUTS = {
  strip: { canvas: [600, 1800], slots: [[20, 20, 560, 373], [20, 413, 560, 373], [20, 806, 560, 373]], footerTop: 1199, doubled: true },
  single: { canvas: [1200, 1800], slots: [[20, 200, 1160, 773]], footerTop: 1000 },
  grid: { canvas: [1200, 1800], slots: [[20, 20, 570, 380], [610, 20, 570, 380], [20, 420, 570, 380], [610, 420, 570, 380]], footerTop: 820 },
};
export const LAYOUT_SHOTS = Object.fromEntries(Object.entries(LAYOUTS).map(([k, v]) => [k, v.slots.length]));
const PRINT = [1200, 1800];
const FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

async function loadImage(blob) {
  const url = URL.createObjectURL(blob);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    return img;
  } finally {
    URL.revokeObjectURL(url);
  }
}

// Scale-to-cover and centre-crop, like Pillow's ImageOps.fit.
function drawCover(ctx, img, x, y, w, h) {
  const scale = Math.max(w / img.naturalWidth, h / img.naturalHeight);
  const sw = w / scale;
  const sh = h / scale;
  const sx = (img.naturalWidth - sw) / 2;
  const sy = (img.naturalHeight - sh) / 2;
  ctx.drawImage(img, sx, sy, sw, sh, x, y, w, h);
}

function fitFont(ctx, text, maxWidth, startSize, minSize = 18) {
  let size = startSize;
  while (size > minSize) {
    ctx.font = `bold ${size}px ${FONT}`;
    if (ctx.measureText(text).width <= maxWidth) return size;
    size -= 4;
  }
  ctx.font = `bold ${minSize}px ${FONT}`;
  return minSize;
}

function drawFooter(ctx, spec, eventName, footerText) {
  const [w, h] = spec.canvas;
  const bandH = h - spec.footerTop;
  const sub = (footerText || '').trim() || new Date().toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' });
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const titleSize = fitFont(ctx, eventName, w - 60, Math.floor(Math.min(bandH * 0.22, w * 0.11)));
  const subSize = fitFont(ctx, sub, w - 60, Math.floor(Math.min(bandH * 0.11, w * 0.055)));
  const gap = Math.floor(bandH * 0.08);
  const block = titleSize + gap + subSize;
  const y = spec.footerTop + Math.floor((bandH - block) / 2);
  ctx.fillStyle = '#1e1e1e';
  ctx.font = `bold ${titleSize}px ${FONT}`;
  ctx.fillText(eventName, w / 2, y);
  ctx.fillStyle = '#6e6e6e';
  ctx.font = `bold ${subSize}px ${FONT}`;
  ctx.fillText(sub, w / 2, y + titleSize + gap);
}

const toBlob = (canvas) => new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92));

/** @returns {Promise<{print: Blob, share: Blob}>} */
export async function compose(layout, photoBlobs, eventName, footerText = '') {
  const spec = LAYOUTS[layout];
  if (!spec) throw new Error(`Unknown layout ${layout}`);
  if (photoBlobs.length < spec.slots.length) throw new Error(`Layout ${layout} needs ${spec.slots.length} photos`);
  const canvas = document.createElement('canvas');
  [canvas.width, canvas.height] = spec.canvas;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  for (let i = 0; i < spec.slots.length; i++) {
    const img = await loadImage(photoBlobs[i]);
    drawCover(ctx, img, ...spec.slots[i]);
  }
  drawFooter(ctx, spec, eventName, footerText);
  const share = await toBlob(canvas);
  if (!spec.doubled) return { print: share, share };
  const sheet = document.createElement('canvas');
  [sheet.width, sheet.height] = PRINT;
  const sctx = sheet.getContext('2d');
  sctx.fillStyle = '#fff';
  sctx.fillRect(0, 0, sheet.width, sheet.height);
  sctx.drawImage(canvas, 0, 0);
  sctx.drawImage(canvas, spec.canvas[0], 0);
  return { print: await toBlob(sheet), share };
}

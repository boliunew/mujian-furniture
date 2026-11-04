// 360° spin: turn 8–72 turntable photos into evenly framed frames, and show them
// as a drag-to-rotate viewer. Processing runs in the browser so the server only
// stores the finished JPEGs.
(function () {
  const natural = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });

  async function loadScaled(file, maxSide) {
    let src;
    try { src = await createImageBitmap(file); }
    catch (e) {
      src = await new Promise((ok, bad) => {
        const img = new Image();
        img.onload = () => ok(img);
        img.onerror = () => bad(new Error(`${file.name} 无法读取（iPhone 的 HEIC 请先导出为 JPG）`));
        img.src = URL.createObjectURL(file);
      });
    }
    const w = src.width, h = src.height, k = Math.min(1, maxSide / Math.max(w, h));
    const c = document.createElement("canvas");
    c.width = Math.round(w * k); c.height = Math.round(h * k);
    c.getContext("2d").drawImage(src, 0, 0, c.width, c.height);
    return c;
  }

  // Background = median color of the four corner patches.
  function background(data, w, h) {
    const s = Math.max(4, Math.round(Math.min(w, h) * 0.04)), px = [];
    for (const [x0, y0] of [[0, 0], [w - s, 0], [0, h - s], [w - s, h - s]])
      for (let y = y0; y < y0 + s; y += 2) for (let x = x0; x < x0 + s; x += 2) {
        const i = (y * w + x) * 4; px.push([data[i], data[i + 1], data[i + 2]]);
      }
    const med = k => px.map(p => p[k]).sort((a, b) => a - b)[px.length >> 1];
    return [med(0), med(1), med(2)];
  }

  // Bounding box of pixels that differ from the background, as fractions of the frame.
  function subjectBox(data, w, h, bg, thr) {
    let x0 = w, y0 = h, x1 = -1, y1 = -1;
    for (let y = 0; y < h; y += 2) for (let x = 0; x < w; x += 2) {
      const i = (y * w + x) * 4;
      if (Math.abs(data[i] - bg[0]) + Math.abs(data[i + 1] - bg[1]) + Math.abs(data[i + 2] - bg[2]) > thr) {
        if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
    }
    if (x1 < 0) return null;
    return { x0: x0 / w, y0: y0 / h, x1: x1 / w, y1: y1 / h };
  }

  async function processSpin(fileList, opts = {}) {
    const { size = 800, whiten = true, onProgress = () => {} } = opts;
    const files = [...fileList].filter(f => /^image\//.test(f.type) || /\.(jpe?g|png|webp)$/i.test(f.name)).sort(natural);
    if (files.length < 8) throw new Error(`至少需要 8 张照片，现在是 ${files.length} 张`);
    if (files.length > 72) throw new Error(`最多 72 张，现在是 ${files.length} 张`);

    const frames = [];
    let box = null;
    for (let i = 0; i < files.length; i++) {
      const c = await loadScaled(files[i], 1400);
      const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
      const bg = background(d, c.width, c.height);
      const b = subjectBox(d, c.width, c.height, bg, 70);
      if (b) box = box ? { x0: Math.min(box.x0, b.x0), y0: Math.min(box.y0, b.y0), x1: Math.max(box.x1, b.x1), y1: Math.max(box.y1, b.y1) } : b;
      frames.push({ c, bg });
      onProgress((i + 1) / files.length * 0.5, `分析第 ${i + 1}/${files.length} 张`);
    }
    box = box || { x0: 0, y0: 0, x1: 1, y1: 1 };

    // One crop for every frame so the piece doesn't jump while turning.
    const out = [];
    for (let i = 0; i < frames.length; i++) {
      const { c, bg } = frames[i];
      const W = c.width, H = c.height;
      let bw = (box.x1 - box.x0) * W, bh = (box.y1 - box.y0) * H;
      const side = Math.max(bw, bh) * 1.12;
      const cx = (box.x0 + box.x1) / 2 * W, cy = (box.y0 + box.y1) / 2 * H;
      const o = document.createElement("canvas"); o.width = o.height = size;
      const g = o.getContext("2d");
      g.fillStyle = whiten ? "#fff" : `rgb(${bg})`; g.fillRect(0, 0, size, size);
      g.imageSmoothingQuality = "high";
      g.drawImage(c, cx - side / 2, cy - side / 2, side, side, 0, 0, size, size);
      if (whiten) {
        const img = g.getImageData(0, 0, size, size), p = img.data;
        for (let k = 0; k < p.length; k += 4) {
          const dist = Math.abs(p[k] - bg[0]) + Math.abs(p[k + 1] - bg[1]) + Math.abs(p[k + 2] - bg[2]);
          if (dist < 36) { p[k] = p[k + 1] = p[k + 2] = 255; }
          else if (dist < 60) {               // soft edge
            const t = (dist - 36) / 24;
            p[k] = p[k] * t + 255 * (1 - t); p[k + 1] = p[k + 1] * t + 255 * (1 - t); p[k + 2] = p[k + 2] * t + 255 * (1 - t);
          }
        }
        g.putImageData(img, 0, 0);
      }
      out.push(await new Promise(r => o.toBlob(r, "image/jpeg", 0.86)));
      onProgress(0.5 + (i + 1) / frames.length * 0.5, `生成第 ${i + 1}/${frames.length} 帧`);
    }
    return { blobs: out, names: files.map(f => f.name) };
  }

  // Drag-to-rotate viewer. Returns a destroy function.
  function mountSpin(el, urls, { autoplay = true } = {}) {
    if (!urls || !urls.length) { el.innerHTML = ""; return () => {}; }
    el.classList.add("spin");
    el.innerHTML = `<img alt="360° view" draggable="false"><div class="spin-hint">↔️ 拖动旋转 · Drag to rotate</div>`;
    const img = el.querySelector("img"), hint = el.querySelector(".spin-hint");
    const cache = urls.map(u => { const i = new Image(); i.src = u; return i; });
    let frame = 0, timer = null, drag = null;
    const show = f => { frame = ((f % urls.length) + urls.length) % urls.length; img.src = cache[frame].src; };
    const stop = () => { clearInterval(timer); timer = null; };
    show(0);
    const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (autoplay && !reduce) timer = setInterval(() => show(frame + 1), 110);
    const down = e => { stop(); hint.hidden = true; drag = { x: e.clientX, f: frame }; el.setPointerCapture?.(e.pointerId); };
    const move = e => {
      if (!drag) return;
      const step = Math.max(4, el.clientWidth / urls.length / 1.2);
      show(drag.f - Math.round((e.clientX - drag.x) / step));
    };
    const up = () => { drag = null; };
    const key = e => { if (e.key === "ArrowLeft") { stop(); show(frame + 1); } if (e.key === "ArrowRight") { stop(); show(frame - 1); } };
    el.tabIndex = 0;
    el.addEventListener("pointerdown", down); el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up); el.addEventListener("keydown", key);
    return () => {
      stop();
      el.removeEventListener("pointerdown", down); el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up); el.removeEventListener("pointercancel", up); el.removeEventListener("keydown", key);
      el.innerHTML = ""; el.classList.remove("spin");
    };
  }

  window.processSpin = processSpin;
  window.mountSpin = mountSpin;
})();

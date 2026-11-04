// 360° spin: turn 8–72 turntable photos (or one turntable video) into evenly framed frames, and show them
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
        img.onerror = () => bad(new Error(`${file.name} ${t("无法读取（iPhone 的 HEIC 请先导出为 JPG）")}`));
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

  const isVideo = f => /^video\//.test(f.type) || /\.(mp4|mov|m4v|webm)$/i.test(f.name);

  // A turntable video: find where one full turn ends, then take `n` evenly spaced frames from it.
  async function videoFrames(file, n, onProgress) {
    const v = document.createElement("video");
    v.muted = true; v.playsInline = true; v.preload = "auto";
    const url = URL.createObjectURL(file);
    v.src = url;
    try {
      await new Promise((ok, bad) => {
        v.onloadeddata = ok;
        v.onerror = () => bad(new Error(t("这个视频浏览器打不开。iPhone 请在 设置 → 相机 → 格式 里选「兼容性最佳」再拍，或先转成 MP4。")));
      });
      const dur = v.duration;
      if (!isFinite(dur) || dur < 2) throw new Error(t("视频太短了，至少要拍完整一圈（建议 10–30 秒）"));
      const seek = s => new Promise(ok => { v.onseeked = ok; v.currentTime = Math.max(0, Math.min(s, dur - 0.05)); });
      const tw = 48, th = Math.max(8, Math.round(48 * v.videoHeight / v.videoWidth));
      const tc = document.createElement("canvas"); tc.width = tw; tc.height = th;
      const tg = tc.getContext("2d", { willReadFrequently: true });
      const thumb = async s => { await seek(s); tg.drawImage(v, 0, 0, tw, th); return tg.getImageData(0, 0, tw, th).data; };
      const diff = (a, b) => { let d = 0; for (let i = 0; i < a.length; i += 4) d += Math.abs(a[i] - b[i]) + Math.abs(a[i + 1] - b[i + 1]) + Math.abs(a[i + 2] - b[i + 2]); return d / (a.length / 4) / 3; };

      // Where does the piece look like the first frame again? That's one full turn.
      const start = Math.min(0.3, dur / 20), ref = await thumb(start);
      const step = Math.max(0.08, dur / 150);
      let best = null;
      for (let s = start + Math.max(1, (dur - start) * 0.35); s < dur - 0.05; s += step) {
        const d = diff(ref, await thumb(s));
        if (!best || d < best.d) best = { s, d };
        onProgress(0.2 * (s - start) / (dur - start), t("🔎 找转完一圈的位置…"));
      }
      if (best) for (let s = best.s - step; s <= best.s + step; s += step / 5) {
        if (s <= start || s >= dur) continue;
        const d = diff(ref, await thumb(s)); if (d < best.d) best = { s, d };
      }
      const found = best && best.d < 12;
      const turn = found ? best.s - start : dur - start;

      const maxSide = 1400, k = Math.min(1, maxSide / Math.max(v.videoWidth, v.videoHeight));
      const out = [];
      for (let i = 0; i < n; i++) {
        await seek(start + turn * i / n);
        const c = document.createElement("canvas");
        c.width = Math.round(v.videoWidth * k); c.height = Math.round(v.videoHeight * k);
        c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
        out.push(c);
        onProgress(0.2 + 0.3 * (i + 1) / n, `${t("截取第")} ${i + 1}/${n} ${t("帧")}`);
      }
      return { canvases: out, turn, found, dur };
    } finally { URL.revokeObjectURL(url); }
  }

  async function processSpin(fileList, opts = {}) {
    const { size = 800, whiten = true, reverse = false, count = 36, onProgress = () => {} } = opts;
    const all = [...fileList];
    const video = all.find(isVideo);
    let sources, info = {};
    if (video) {
      if (count < 8 || count > 72) throw new Error(t("帧数要在 8 到 72 之间"));
      const r = await videoFrames(video, count, onProgress);
      sources = r.canvases; info = { video: true, turn: r.turn, found: r.found, dur: r.dur };
    } else {
      const files = all.filter(f => /^image\//.test(f.type) || /\.(jpe?g|png|webp)$/i.test(f.name)).sort(natural);
      if (files.length < 8) throw new Error(`${t("至少需要 8 张照片，现在是")} ${files.length} ${t("张")}`);
      if (files.length > 72) throw new Error(`${t("最多 72 张，现在是")} ${files.length} ${t("张")}`);
      sources = files;
      info = { photos: files.length, mismatch: files.length !== count };
    }
    if (reverse) sources = sources.slice().reverse();

    const frames = [];
    let box = null;
    for (let i = 0; i < sources.length; i++) {
      const c = video ? sources[i] : await loadScaled(sources[i], 1400);
      const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
      const bg = background(d, c.width, c.height);
      const b = subjectBox(d, c.width, c.height, bg, 70);
      if (b) box = box ? { x0: Math.min(box.x0, b.x0), y0: Math.min(box.y0, b.y0), x1: Math.max(box.x1, b.x1), y1: Math.max(box.y1, b.y1) } : b;
      frames.push({ c, bg });
      onProgress(video ? 0.5 + (i + 1) / sources.length * 0.1 : (i + 1) / sources.length * 0.5, `${t("分析第")} ${i + 1}/${sources.length} ${t("张")}`);
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
            const tb = (dist - 36) / 24;
            p[k] = p[k] * tb + 255 * (1 - tb); p[k + 1] = p[k + 1] * tb + 255 * (1 - tb); p[k + 2] = p[k + 2] * tb + 255 * (1 - tb);
          }
        }
        g.putImageData(img, 0, 0);
      }
      out.push(await new Promise(r => o.toBlob(r, "image/jpeg", 0.86)));
      onProgress(0.5 + (i + 1) / frames.length * 0.5, `${t("生成第")} ${i + 1}/${frames.length} ${t("帧")}`);
    }
    return { blobs: out, ...info };
  }

  // Drag-to-rotate viewer. Returns a destroy function.
  function mountSpin(el, urls, { autoplay = true } = {}) {
    if (!urls || !urls.length) { el.innerHTML = ""; return () => {}; }
    el.classList.add("spin");
    el.innerHTML = `<img alt="360° view" draggable="false"><div class="spin-hint">${t("↔️ 拖动旋转 · Drag to rotate")}</div>`;
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

  // A printable angle dial to put under (or tape around) a turntable: one numbered mark per photo.
  function spinDialHTML(n) {
    const R = 340, marks = [];
    for (let i = 0; i < n; i++) {
      const a = (i * 360 / n - 90) * Math.PI / 180, big = (i * 360 / n) % 90 === 0;
      const r1 = big ? R - 46 : R - 26;
      marks.push(`<line x1="${(Math.cos(a) * r1).toFixed(1)}" y1="${(Math.sin(a) * r1).toFixed(1)}" x2="${(Math.cos(a) * R).toFixed(1)}" y2="${(Math.sin(a) * R).toFixed(1)}" stroke="#000" stroke-width="${big ? 4 : 2}"/>`);
      const rt = R + 22;
      marks.push(`<text x="${(Math.cos(a) * rt).toFixed(1)}" y="${(Math.sin(a) * rt + 6).toFixed(1)}" font-size="${n > 40 ? 11 : 16}" text-anchor="middle" font-family="Arial" font-weight="${big ? 700 : 400}">${i + 1}</text>`);
    }
    const deg = +(360 / n).toFixed(2);
    return `<!doctype html><html><head><meta charset="utf-8"><title>${t("转盘刻度纸")} ${n}</title>
<style>@page{size:letter;margin:.4in}body{font-family:Arial,sans-serif;margin:0;text-align:center}svg{width:7.4in;height:7.4in}
h1{font-size:20px;margin:.1in 0}p{font-size:13px;margin:4px 0;color:#333}</style></head><body>
<h1>📐 ${t("转盘刻度纸")} · ${n} ${t("张")} · ${t("每")} ${deg}°</h1>
<svg viewBox="-400 -400 800 800"><circle r="${R}" fill="none" stroke="#000" stroke-width="2"/><circle r="6" fill="#000"/>
<line x1="-30" y1="0" x2="30" y2="0" stroke="#000"/><line x1="0" y1="-30" x2="0" y2="30" stroke="#000"/>
${marks.join("")}</svg>
<p>${t("① 用 100% 比例打印（不要“适合页面”），剪下圆盘，中心对准转盘中心贴好。")}</p>
<p>${t("② 在转盘外面固定一个指针（贴一条胶带），对准 1 号刻度拍第一张。")}</p>
<p>${t("③ 每转到下一个刻度拍一张，拍到最后一个号就停，不要回到 1 号再拍。")}</p>
<script>onload=()=>setTimeout(()=>print(),300)<\/script></body></html>`;
  }
  function openDial(n) {
    const url = URL.createObjectURL(new Blob([spinDialHTML(n)], { type: "text/html" }));
    const w = window.open(url, "_blank");
    if (!w) { const a = document.createElement("a"); a.href = url; a.download = `turntable-dial-${n}.html`; a.click(); }
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  // Turn the frames into a short video (MP4 where the browser can, otherwise WebM) for social media.
  async function exportSpinVideo(urls, { size = 1080, turns = 2, seconds = 5, onProgress = () => {} } = {}) {
    if (!window.MediaRecorder || !HTMLCanvasElement.prototype.captureStream) throw new Error(t("这个浏览器不支持导出视频，请用电脑上的 Chrome 或 Edge"));
    const imgs = await Promise.all(urls.map(u => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => bad(new Error(t("有一帧图片读不出来"))); i.src = u; })));
    const mime = ["video/mp4;codecs=avc1", "video/mp4", "video/webm;codecs=vp9", "video/webm"].find(m => MediaRecorder.isTypeSupported(m));
    if (!mime) throw new Error(t("这个浏览器不支持导出视频，请用电脑上的 Chrome 或 Edge"));
    const c = document.createElement("canvas"); c.width = c.height = size;
    const g = c.getContext("2d"); g.fillStyle = "#fff"; g.fillRect(0, 0, size, size);
    const draw = img => { g.fillStyle = "#fff"; g.fillRect(0, 0, size, size); g.drawImage(img, 0, 0, size, size); };
    draw(imgs[0]);
    const stream = c.captureStream(30), rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 8e6 });
    const chunks = []; rec.ondataavailable = e => e.data.size && chunks.push(e.data);
    const done = new Promise(ok => { rec.onstop = ok; });
    rec.start(250);
    const per = seconds * 1000 / imgs.length, total = imgs.length * turns, t0 = performance.now();
    for (let k = 0; k < total; k++) {
      draw(imgs[k % imgs.length]);
      onProgress((k + 1) / total);
      const wait = t0 + (k + 1) * per - performance.now();
      await new Promise(r => setTimeout(r, Math.max(0, wait)));
    }
    rec.stop(); await done; stream.getTracks().forEach(tr => tr.stop());
    const type = mime.split(";")[0];
    return { blob: new Blob(chunks, { type }), ext: type === "video/mp4" ? "mp4" : "webm" };
  }

  window.processSpin = processSpin;
  window.openDial = openDial;
  window.exportSpinVideo = exportSpinVideo;
  window.mountSpin = mountSpin;
})();

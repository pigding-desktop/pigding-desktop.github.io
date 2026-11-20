const PART_BYTES = 4 * 1024 * 1024;
const LANES = 4;
const MAX_BYTES = 384 * 1024 * 1024;

class RangeUnavailable extends Error {}

export function readManifest(meta, manifestUrl, expectedFilename) {
  const { filename, mirrorUrl, sizeBytes, sha256 } = meta;
  if (filename !== expectedFilename || !/^PIGDING-\d+\.\d+\.\d+-win-x64\.zip$/.test(filename) ||
      mirrorUrl !== `downloads/${filename}` || !Number.isSafeInteger(sizeBytes) ||
      sizeBytes <= 0 || sizeBytes > MAX_BYTES || !/^[a-f0-9]{64}$/.test(sha256)) {
    throw new Error("Invalid download metadata");
  }
  return { filename, url: new URL(mirrorUrl, manifestUrl), sizeBytes, sha256 };
}

export async function downloadParts(file, userSignal, onProgress) {
  const bytes = new Uint8Array(file.sizeBytes);
  const controller = new AbortController();
  const signal = AbortSignal.any([userSignal, controller.signal]);
  let next = 0;
  let received = 0;

  async function readPart(start, end) {
    for (let attempt = 0; ; attempt++) {
      signal.throwIfAborted();
      let loaded = 0;
      try {
        const response = await fetch(file.url, {
          headers: { Range: `bytes=${start}-${end}` },
          cache: "no-store", redirect: "error",
          signal: AbortSignal.any([signal, AbortSignal.timeout(180_000)]),
        });
        if (response.status !== 206 ||
            response.headers.get("Content-Range") !== `bytes ${start}-${end}/${bytes.length}` || !response.body) {
          response.body?.cancel().catch(() => {});
          if (response.status === 429 || response.status >= 500) throw new Error("Temporary server error");
          throw new RangeUnavailable("The server did not return the requested range");
        }
        const reader = response.body.getReader();
        try {
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            if (start + loaded + value.length > end + 1) throw new RangeUnavailable("Range is too large");
            bytes.set(value, start + loaded);
            loaded += value.length;
            received += value.length;
            onProgress(received, bytes.length);
          }
        } catch (error) {
          reader.cancel().catch(() => {});
          throw error;
        } finally {
          reader.releaseLock();
        }
        if (loaded !== end - start + 1) throw new Error("Incomplete range");
        return;
      } catch (error) {
        received -= loaded;
        if (signal.aborted || attempt === 2 || error instanceof RangeUnavailable) throw error;
        onProgress(received, bytes.length);
        // Let transient errors settle before re-requesting this part.
        await new Promise(resolve => setTimeout(resolve, 600 * (attempt + 1)));
      }
    }
  }

  async function lane() {
    while (next < bytes.length) {
      const start = next;
      next += PART_BYTES;
      await readPart(start, Math.min(next, bytes.length) - 1);
    }
  }

  try {
    await Promise.all(Array.from({ length: LANES }, lane));
    signal.throwIfAborted();
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
    signal.throwIfAborted();
    const hex = Array.from(digest, byte => byte.toString(16).padStart(2, "0")).join("");
    if (hex !== file.sha256) throw new Error("SHA-256 mismatch");
    return new Blob([bytes], { type: "application/zip" });
  } finally {
    controller.abort();
  }
}

function init() {
  const widget = document.getElementById("fast-download");
  if (widget?.dataset.enabled !== "yes" || !globalThis.crypto?.subtle ||
      !globalThis.AbortSignal?.any || !AbortSignal.timeout || !AbortSignal.prototype.throwIfAborted) return;

  const jump = document.getElementById("fast-jump");
  const start = document.getElementById("fast-start");
  const cancel = document.getElementById("fast-cancel");
  const save = document.getElementById("fast-save");
  const status = document.getElementById("fast-status");
  const meter = document.getElementById("fast-meter");
  const count = document.getElementById("fast-count");
  const expectedFile = document.querySelector(".download-actions a").download;
  let readyUrl;
  let active;
  let lastPaint;
  const mib = bytes => (bytes / 1024 ** 2).toFixed(1);

  function releaseObjectUrl() {
    if (readyUrl) URL.revokeObjectURL(readyUrl);
    readyUrl = undefined;
    save.removeAttribute("href");
    save.hidden = true;
  }

  widget.hidden = false;
  jump.hidden = false;

  cancel.addEventListener("click", () => {
    active?.abort();
    status.textContent = "已取消分段下载。";
    active = undefined;
    start.disabled = false;
    cancel.hidden = true;
    meter.hidden = true;
  });

  start.addEventListener("click", async () => {
    if (active) return;
    releaseObjectUrl();
    const controller = new AbortController();
    const { signal } = controller;
    active = controller;
    start.disabled = true;
    cancel.hidden = false;
    meter.hidden = true;
    status.textContent = "正在获取下载信息…";
    lastPaint = -Infinity;
    try {
      const manifestUrl = new URL("release.json", document.baseURI);
      const response = await fetch(manifestUrl, {
        cache: "no-store", redirect: "error",
        signal: AbortSignal.any([signal, AbortSignal.timeout(20_000)]),
      });
      if (!response.ok) throw new Error("Could not load release manifest");
      const file = readManifest(await response.json(), manifestUrl, expectedFile);
      signal.throwIfAborted();
      meter.hidden = false;
      status.textContent = "正在分段下载，请保持网页打开。";
      function paint(received, total) {
        if (active !== controller) return;
        const now = performance.now();
        if (now - lastPaint < 250 && received < total) return;
        lastPaint = now;
        meter.value = received / total;
        count.textContent = `${mib(received)} / ${mib(total)} MiB`;
        if (received === total) status.textContent = "下载完成，正在校验文件…";
      }
      paint(0, file.sizeBytes);
      const zip = await downloadParts(file, signal, paint);
      if (active !== controller) return;
      readyUrl = URL.createObjectURL(zip);
      save.href = readyUrl;
      save.download = file.filename;
      save.hidden = false;
      status.textContent = "文件已校验，点击「保存 ZIP」即可保存。";
      save.focus();
    } catch {
      if (active !== controller) return;
      meter.hidden = true;
      status.textContent = "分段下载未完成。可重试，或使用上方的普通下载。";
    } finally {
      if (active === controller) {
        active = undefined;
        start.disabled = false;
        cancel.hidden = true;
      }
    }
  });
}

if (typeof document !== "undefined") init();

(function () {
  const MAX_CONCURRENT = 2;
  const RETRY_DELAYS_MS = [1000, 2000, 4000];
  const queue = [];
  let active = 0;

  function abortReason(signal, fallback) {
    const reason = signal && signal.reason;
    return typeof reason === "string" && reason ? reason : (fallback || "aborted");
  }

  function abortError(reason) {
    const error = new Error("图片加载已取消");
    error.name = "AbortError";
    error.reason = reason || "aborted";
    return error;
  }

  function emitState(options, state, detail) {
    if (!options || typeof options.onState !== "function") return;
    try {
      options.onState(state, detail || {});
    } catch (_error) {
      // Rendering callbacks must never break the shared image pipeline.
    }
  }

  function enqueue(run, signal) {
    return new Promise(function (resolve, reject) {
      queue.push({ run: run, signal: signal, resolve: resolve, reject: reject });
      pump();
    });
  }

  function pump() {
    while (active < MAX_CONCURRENT && queue.length) {
      const task = queue.shift();
      if (task.signal && task.signal.aborted) {
        task.reject(abortError(abortReason(task.signal)));
        continue;
      }
      active += 1;
      Promise.resolve()
        .then(task.run)
        .then(task.resolve, task.reject)
        .finally(function () {
          active = Math.max(0, active - 1);
          pump();
        });
    }
  }

  function wait(ms, signal) {
    return new Promise(function (resolve, reject) {
      if (signal && signal.aborted) {
        reject(abortError(abortReason(signal)));
        return;
      }
      let settled = false;
      const finish = function (callback) {
        if (settled) return;
        settled = true;
        if (signal) signal.removeEventListener("abort", onAbort);
        callback();
      };
      const timer = window.setTimeout(function () { finish(resolve); }, ms);
      const onAbort = function () {
        window.clearTimeout(timer);
        finish(function () { reject(abortError(abortReason(signal))); });
      };
      if (signal) {
        signal.addEventListener("abort", onAbort, { once: true });
      }
    });
  }

  function retryAfterMs(response, fallbackMs) {
    const seconds = Number(response && response.headers && response.headers.get("Retry-After") || 0);
    return seconds > 0 ? seconds * 1000 : fallbackMs;
  }

  function responseError(message, response, retryable, code) {
    const error = new Error(message || "图片加载失败");
    error.status = Number(response && response.status || 0);
    error.retryable = Boolean(retryable);
    error.code = code || "image_request_failed";
    return error;
  }

  async function fetchImageBlob(url, options) {
    const signal = options && options.signal;
    let lastError = null;
    for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt += 1) {
      if (signal && signal.aborted) throw abortError(abortReason(signal));
      try {
        emitState(options, "loading", { attempt: attempt + 1 });
        const result = await enqueue(async function () {
          const response = await fetch(url, {
            credentials: "same-origin",
            cache: attempt === 0 ? "force-cache" : "reload",
            headers: { Accept: "image/avif,image/webp,image/png,image/jpeg,image/*" },
            signal: signal,
          });
          const pending = String(response.headers.get("X-AICRM-Media-Generation") || "").toLowerCase() === "pending";
          if (pending && response.body && typeof response.body.cancel === "function") response.body.cancel();
          const blob = response.ok && !pending ? await response.blob() : null;
          return { response: response, pending: pending, blob: blob };
        }, signal);
        if (result.pending) {
          const pendingError = responseError("图片仍在生成", result.response, true, "image_generation_pending");
          pendingError.retryAfterMs = retryAfterMs(result.response, RETRY_DELAYS_MS[attempt]);
          emitState(options, "pending", { attempt: attempt + 1 });
          throw pendingError;
        }
        if (result.response.ok) {
          const blob = result.blob;
          if (!blob || !blob.size || !String(blob.type || "").toLowerCase().startsWith("image/")) {
            throw responseError("图片响应无效", result.response, false, "image_response_invalid");
          }
          return blob;
        }
        const retryable = result.response.status === 429 || result.response.status === 503;
        const error = responseError("图片加载失败", result.response, retryable);
        error.retryAfterMs = retryAfterMs(result.response, RETRY_DELAYS_MS[attempt]);
        throw error;
      } catch (error) {
        if (error && error.name === "AbortError") throw error;
        if (error instanceof TypeError) {
          error.retryable = true;
          error.code = "image_network_error";
        }
        lastError = error;
        if (!error.retryable || attempt >= RETRY_DELAYS_MS.length) throw error;
        const delay = error.retryAfterMs || RETRY_DELAYS_MS[attempt];
        emitState(options, "retrying", { attempt: attempt + 1, delayMs: delay, code: error.code || "" });
        await wait(delay, signal);
      }
    }
    throw lastError || new Error("图片加载失败");
  }

  function applyBlobToImage(image, blob, signal) {
    return new Promise(function (resolve, reject) {
      const objectUrl = URL.createObjectURL(blob);
      if (signal && signal.aborted) {
        URL.revokeObjectURL(objectUrl);
        reject(abortError(abortReason(signal)));
        return;
      }
      let settled = false;
      const cleanup = function () {
        image.removeEventListener("load", onLoad);
        image.removeEventListener("error", onError);
        if (signal) signal.removeEventListener("abort", onAbort);
        URL.revokeObjectURL(objectUrl);
      };
      const finish = function (callback) {
        if (settled) return;
        settled = true;
        cleanup();
        callback();
      };
      const onLoad = function () { finish(resolve); };
      const onError = function () {
        const error = new Error("图片解码失败");
        error.code = "image_decode_failed";
        error.retryable = false;
        finish(function () { reject(error); });
      };
      const onAbort = function () {
        finish(function () { reject(abortError(abortReason(signal))); });
      };
      image.addEventListener("load", onLoad, { once: true });
      image.addEventListener("error", onError, { once: true });
      if (signal) signal.addEventListener("abort", onAbort, { once: true });
      image.src = objectUrl;
    });
  }

  function loadInto(image, url, options) {
    if (!image || !url) return Promise.resolve(false);
    options = options || {};
    const parentSignal = options && options.signal;
    const localController = typeof AbortController !== "undefined" ? new AbortController() : null;
    const signal = localController ? localController.signal : parentSignal;
    let detachParent = null;
    if (localController && parentSignal) {
      const abortFromParent = function () { localController.abort("parent"); };
      if (parentSignal.aborted) abortFromParent();
      else {
        parentSignal.addEventListener("abort", abortFromParent, { once: true });
        detachParent = function () { parentSignal.removeEventListener("abort", abortFromParent); };
      }
    }
    let observer = null;
    let enteredRange = false;
    let finished = false;
    if (localController && options && options.cancelOutsideViewport && typeof IntersectionObserver !== "undefined") {
      observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) enteredRange = true;
          else if (enteredRange && !finished) localController.abort("outside_viewport");
        });
      }, { rootMargin: String(options.rootMargin || "60px 0px") });
      observer.observe(image);
    }
    emitState(options, "queued", {});
    return fetchImageBlob(url, { signal: signal, onState: options.onState }).then(async function (blob) {
      if (signal && signal.aborted) throw abortError(abortReason(signal));
      await applyBlobToImage(image, blob, signal);
      finished = true;
      emitState(options, "ready", {});
      return true;
    }).catch(function (error) {
      emitState(options, error && error.name === "AbortError" ? "aborted" : "error", {
        reason: error && error.reason || "",
        code: error && error.code || "",
        retryable: Boolean(error && error.retryable),
      });
      throw error;
    }).finally(function () {
      if (observer) observer.disconnect();
      if (detachParent) detachParent();
    });
  }

  function createPager(options) {
    const container = options.container;
    const scrollTarget = options.scrollTarget || window;
    const pageSize = Math.max(1, Number(options.pageSize || 5));
    const cooldownMs = Math.max(0, Number(options.cooldownMs || 300));
    const sentinel = document.createElement("div");
    sentinel.className = options.sentinelClass || "image-page-sentinel";
    sentinel.setAttribute("aria-live", "polite");
    let offset = Math.max(0, Number(options.initialOffset || 0));
    let hasMore = options.initialHasMore !== false;
    let loading = false;
    let destroyed = false;
    let interactionVersion = 0;
    let loadedInteractionVersion = 0;
    let lastLoadAt = 0;
    let controller = null;
    let retryAttempt = 0;
    let retryTimer = null;

    function mountSentinel() {
      if (!container || destroyed) return;
      if (sentinel.parentNode !== container) container.appendChild(sentinel);
      sentinel.hidden = !hasMore;
      sentinel.textContent = loading
        ? "正在加载更多…"
        : (retryTimer ? "加载失败，稍后自动重试" : (hasMore ? "继续滚动加载更多" : ""));
    }

    function visible() {
      if (sentinel.hidden || !sentinel.isConnected) return false;
      const rect = sentinel.getBoundingClientRect();
      if (scrollTarget !== window && scrollTarget.getBoundingClientRect) {
        const rootRect = scrollTarget.getBoundingClientRect();
        return rect.top <= rootRect.bottom + 60 && rect.bottom >= rootRect.top - 60;
      }
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
      return rect.top <= viewportHeight + 60 && rect.bottom >= -60;
    }

    async function loadNext(forceInitial) {
      if (destroyed || loading || (!forceInitial && !hasMore)) return false;
      if (!forceInitial && interactionVersion <= loadedInteractionVersion) return false;
      const elapsed = Date.now() - lastLoadAt;
      if (!forceInitial && elapsed < cooldownMs) {
        window.setTimeout(function () { if (visible()) loadNext(false); }, cooldownMs - elapsed);
        return false;
      }
      loading = true;
      if (!forceInitial) loadedInteractionVersion = interactionVersion;
      controller = typeof AbortController !== "undefined" ? new AbortController() : null;
      mountSentinel();
      if (options.onLoading) options.onLoading(true, offset > 0);
      try {
        const payload = await options.fetchPage({
          limit: pageSize,
          offset: offset,
          signal: controller ? controller.signal : undefined,
        });
        if (destroyed) return false;
        const items = Array.isArray(payload.items) ? payload.items : [];
        const append = offset > 0;
        if (options.onPage) options.onPage(items, payload, append);
        const derivedOffset = offset + items.length;
        offset = payload.next_offset === null || payload.next_offset === undefined
          ? derivedOffset
          : Number(payload.next_offset);
        hasMore = typeof payload.has_more === "boolean"
          ? payload.has_more
          : derivedOffset < Number(payload.total || derivedOffset);
        lastLoadAt = Date.now();
        retryAttempt = 0;
        return true;
      } catch (error) {
        if (!(error && error.name === "AbortError") && options.onError) options.onError(error, offset > 0);
        if (!(error && error.name === "AbortError") && offset > 0 && retryAttempt < RETRY_DELAYS_MS.length) {
          const delay = RETRY_DELAYS_MS[retryAttempt];
          retryAttempt += 1;
          retryTimer = window.setTimeout(function () {
            retryTimer = null;
            loadNext(true);
          }, delay);
        }
        return false;
      } finally {
        loading = false;
        controller = null;
        mountSentinel();
        if (options.onLoading) options.onLoading(false, offset > 0);
      }
    }

    function recordInteraction(event) {
      if (destroyed || (event && event.isTrusted === false)) return;
      interactionVersion += 1;
      if (visible()) loadNext(false);
    }

    const observer = typeof IntersectionObserver !== "undefined"
      ? new IntersectionObserver(function (entries) {
          if (entries.some(function (entry) { return entry.isIntersecting; })) loadNext(false);
        }, { root: scrollTarget === window ? null : scrollTarget, rootMargin: "60px 0px" })
      : null;

    ["scroll", "wheel", "touchmove", "keydown"].forEach(function (name) {
      scrollTarget.addEventListener(name, recordInteraction, { passive: true });
    });
    mountSentinel();
    if (observer) observer.observe(sentinel);

    return {
      loadInitial: function () { return loadNext(true); },
      destroy: function () {
        destroyed = true;
        if (controller) controller.abort();
        if (retryTimer) window.clearTimeout(retryTimer);
        if (observer) observer.disconnect();
        ["scroll", "wheel", "touchmove", "keydown"].forEach(function (name) {
          scrollTarget.removeEventListener(name, recordInteraction);
        });
        sentinel.remove();
      },
      sentinel: sentinel,
    };
  }

  window.ImageResourceLoader = {
    loadInto: loadInto,
    createPager: createPager,
    maxConcurrent: MAX_CONCURRENT,
  };
})();

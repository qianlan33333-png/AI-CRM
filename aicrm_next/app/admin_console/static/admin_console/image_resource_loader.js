(function () {
  const MAX_CONCURRENT = 2;
  const RETRY_DELAYS_MS = [1000, 2000, 4000];
  const queue = [];
  let active = 0;

  function abortError() {
    const error = new Error("图片加载已取消");
    error.name = "AbortError";
    return error;
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
        task.reject(abortError());
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
        reject(abortError());
        return;
      }
      const timer = window.setTimeout(resolve, ms);
      if (signal) {
        signal.addEventListener("abort", function () {
          window.clearTimeout(timer);
          reject(abortError());
        }, { once: true });
      }
    });
  }

  async function fetchImageBlob(url, options) {
    const signal = options && options.signal;
    let lastError = null;
    for (let attempt = 0; attempt <= RETRY_DELAYS_MS.length; attempt += 1) {
      if (signal && signal.aborted) throw abortError();
      try {
        const response = await fetch(url, {
          credentials: "same-origin",
          cache: "force-cache",
          headers: { Accept: "image/avif,image/webp,image/png,image/jpeg,image/*" },
          signal: signal,
        });
        if (response.ok) return await response.blob();
        const retryable = response.status === 429 || response.status === 503;
        const error = new Error("图片加载失败");
        error.status = response.status;
        error.retryable = retryable;
        const retryAfter = Number(response.headers.get("Retry-After") || 0);
        error.retryAfterMs = retryAfter > 0 ? retryAfter * 1000 : 0;
        throw error;
      } catch (error) {
        if (error && error.name === "AbortError") throw error;
        lastError = error;
        if (!error.retryable || attempt >= RETRY_DELAYS_MS.length) throw error;
        await wait(error.retryAfterMs || RETRY_DELAYS_MS[attempt], signal);
      }
    }
    throw lastError || new Error("图片加载失败");
  }

  function loadInto(image, url, options) {
    if (!image || !url) return Promise.resolve(false);
    const parentSignal = options && options.signal;
    const localController = typeof AbortController !== "undefined" ? new AbortController() : null;
    const signal = localController ? localController.signal : parentSignal;
    let detachParent = null;
    if (localController && parentSignal) {
      const abortFromParent = function () { localController.abort(); };
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
          else if (enteredRange && !finished) localController.abort();
        });
      }, { rootMargin: String(options.rootMargin || "60px 0px") });
      observer.observe(image);
    }
    return enqueue(async function () {
      const blob = await fetchImageBlob(url, { signal: signal });
      if (signal && signal.aborted) throw abortError();
      const objectUrl = URL.createObjectURL(blob);
      image.addEventListener("load", function () { URL.revokeObjectURL(objectUrl); }, { once: true });
      image.src = objectUrl;
      finished = true;
      return true;
    }, signal).finally(function () {
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

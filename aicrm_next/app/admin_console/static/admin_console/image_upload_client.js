(function (window) {
  "use strict";

  var MAX_SOURCE_BYTES = 2 * 1024 * 1024;
  var DIRECT_UPLOAD_BYTES = 900 * 1024;
  var MAX_CANVAS_SIDE = 1600;
  var MIN_CANVAS_SIDE = 720;
  var JPEG_MIME = "image/jpeg";
  var DEFAULT_REQUEST_TIMEOUT_MS = 15000;

  function headersToObject(headers) {
    var result = {};
    if (!headers) return result;
    try {
      new Headers(headers).forEach(function (value, key) {
        result[key] = value;
      });
      return result;
    } catch (_error) {
      Object.keys(headers).forEach(function (key) {
        result[key] = headers[key];
      });
      return result;
    }
  }

  function hasHeader(headers, name) {
    var normalized = String(name || "").toLowerCase();
    return Object.keys(headers).some(function (key) {
      return key.toLowerCase() === normalized;
    });
  }

  function parseJson(text) {
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch (_error) {
      return null;
    }
  }

  function buildNonJsonMessage(response) {
    if (response && response.status === 413) {
      return "图片超过线上上传限制，已被网关拒绝。请压缩后重试。";
    }
    if (response && (response.redirected || response.status === 401 || response.status === 403)) {
      return "登录已过期或无权限，请刷新页面后重新登录。";
    }
    if (response && response.status) {
      return "服务返回了非 JSON 错误页（HTTP " + response.status + "）。";
    }
    return "服务返回了非 JSON 响应。";
  }

  function requestJson(url, options) {
    options = options || {};
    var headers = headersToObject(options.headers);
    if (!hasHeader(headers, "Accept")) headers.Accept = "application/json";
    if (!hasHeader(headers, "X-Requested-With")) headers["X-Requested-With"] = "XMLHttpRequest";

    var finalOptions = {};
    Object.keys(options).forEach(function (key) {
      if (key === "timeoutMs") return;
      finalOptions[key] = options[key];
    });
    finalOptions.credentials = options.credentials || "same-origin";
    finalOptions.headers = headers;
    var timeoutMs = Number(options.timeoutMs || DEFAULT_REQUEST_TIMEOUT_MS);
    var controller = new AbortController();
    var timeoutId = null;
    var timedOut = false;
    var sourceSignal = options.signal;
    if (sourceSignal) {
      if (sourceSignal.aborted) controller.abort();
      else sourceSignal.addEventListener("abort", function () { controller.abort(); }, { once: true });
    }
    if (timeoutMs > 0) {
      timeoutId = setTimeout(function () {
        timedOut = true;
        controller.abort();
      }, timeoutMs);
    }
    finalOptions.signal = controller.signal;

    return fetch(url, finalOptions).then(function (response) {
      return response.text().then(function (text) {
        var payload = parseJson(text);
        if (!payload) {
          var error = new Error(buildNonJsonMessage(response));
          error.status = response.status;
          error.responseText = text;
          throw error;
        }
        if (!response.ok && payload.ok !== false) {
          payload.ok = false;
          payload.error = payload.error || response.statusText || ("HTTP " + response.status);
        }
        return payload;
      });
    }).catch(function (error) {
      if (timedOut || (error && error.name === "AbortError" && !sourceSignal)) {
        throw new Error("请求超时，请检查网络或稍后重试");
      }
      throw error;
    }).finally(function () {
      if (timeoutId) clearTimeout(timeoutId);
    });
  }

  function canvasToBlob(canvas, mimeType, quality) {
    return new Promise(function (resolve, reject) {
      canvas.toBlob(function (blob) {
        if (blob) resolve(blob);
        else reject(new Error("图片压缩失败"));
      }, mimeType, quality);
    });
  }

  function loadImage(file) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(file);
      var img = new Image();
      img.onload = function () {
        URL.revokeObjectURL(url);
        resolve(img);
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error("图片读取失败，请换一张图片重试。"));
      };
      img.src = url;
    });
  }

  function resizedRect(width, height, maxSide) {
    var longest = Math.max(width, height);
    if (!longest || longest <= maxSide) return { width: width, height: height };
    var scale = maxSide / longest;
    return {
      width: Math.max(1, Math.round(width * scale)),
      height: Math.max(1, Math.round(height * scale)),
    };
  }

  function replaceExtension(fileName, ext) {
    var name = String(fileName || "image-library-asset");
    return name.replace(/\.[^.]+$/, "") + ext;
  }

  function isSupportedSourceImage(file) {
    var type = String(file && file.type || "").toLowerCase();
    if (type === "image/jpeg" || type === "image/jpg" || type === "image/png") return true;
    return /\.(jpe?g|png)$/i.test(String(file && file.name || ""));
  }

  async function compressImage(file) {
    var img = await loadImage(file);
    var maxSide = Math.min(
      MAX_CANVAS_SIDE,
      Math.max(MIN_CANVAS_SIDE, img.naturalWidth || img.width, img.naturalHeight || img.height)
    );
    var canvas = document.createElement("canvas");
    var ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) throw new Error("当前浏览器不支持图片压缩，请先把图片压缩到 900KB 内。");

    while (maxSide >= MIN_CANVAS_SIDE) {
      var rect = resizedRect(img.naturalWidth || img.width, img.naturalHeight || img.height, maxSide);
      canvas.width = rect.width;
      canvas.height = rect.height;
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, rect.width, rect.height);
      ctx.drawImage(img, 0, 0, rect.width, rect.height);

      for (var quality = 0.86; quality >= 0.58; quality -= 0.08) {
        var blob = await canvasToBlob(canvas, JPEG_MIME, quality);
        if (blob.size <= DIRECT_UPLOAD_BYTES) {
          return new File([blob], replaceExtension(file.name, ".jpg"), {
            type: JPEG_MIME,
            lastModified: Date.now(),
          });
        }
      }
      maxSide = Math.floor(maxSide * 0.82);
    }

    throw new Error("图片压缩后仍超过上传限制，请先压缩到 900KB 内再上传。");
  }

  async function prepareImageForUpload(file) {
    if (!file) throw new Error("请先选择文件");
    if (!String(file.type || "").toLowerCase().startsWith("image/")) {
      throw new Error("只能上传图片文件");
    }
    if (!isSupportedSourceImage(file)) {
      throw new Error("只能上传 JPG/PNG 图片");
    }
    if (file.size > MAX_SOURCE_BYTES) {
      throw new Error("图片大小不能超过 2MB");
    }
    if (file.size <= DIRECT_UPLOAD_BYTES) {
      return { file: file, compressed: false, originalSize: file.size, uploadSize: file.size };
    }

    var compressed = await compressImage(file);
    return {
      file: compressed,
      compressed: true,
      originalSize: file.size,
      uploadSize: compressed.size,
    };
  }

  function fmtBytes(n) {
    n = Number(n) || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(2) + " MB";
  }

  window.ImageUploadClient = {
    MAX_SOURCE_BYTES: MAX_SOURCE_BYTES,
    DIRECT_UPLOAD_BYTES: DIRECT_UPLOAD_BYTES,
    requestJson: requestJson,
    requestJsonWithTimeout: requestJson,
    prepareImageForUpload: prepareImageForUpload,
    fmtBytes: fmtBytes,
  };
})(window);

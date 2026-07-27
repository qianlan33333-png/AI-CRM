/**
 * 图片素材库通用 picker 组件
 *
 * 任何需要"选图片"的位置（小程序卡片缩略图 / campaign step 编辑 / 群发任务等）
 * 都引用此文件，调用 `mountImagePicker(container, options)` 挂载。
 *
 * Options:
 *   mode: 'single' | 'multiple'   单选还是多选（默认 single）
 *   max: number                    多选时上限（默认 9，对齐企微单消息图片上限）
 *   value: number | number[]       初始选中的 image_library.id
 *   onChange: (ids) => void        选中变化回调，single 模式给单个 id（或 0），multiple 给数组
 *
 * DOM 结构：
 *   - 已选缩略图列表
 *   - "上传图片" 按钮（自动入素材库 + 选中）
 *   - "从素材库选" 按钮（弹层列出全部启用的图片）
 *
 * 容器只渲染需要的 UI，不强行造样式架构 — 由调用方提供外层 label。
 */
(function () {
  let _libraryCache = null;
  let _libraryCachePromise = null;

  function requestJson(url, options) {
    if (window.ImageUploadClient && window.ImageUploadClient.requestJson) {
      return window.ImageUploadClient.requestJson(url, options || {});
    }
    return fetch(url, options || { credentials: 'same-origin' }).then(function (resp) {
      return resp.text().then(function (text) {
        try { return text ? JSON.parse(text) : {}; }
        catch (e) { return { ok: false, error: '服务返回了非 JSON 响应' }; }
      });
    });
  }

  async function fetchLibrary({ force = false } = {}) {
    if (!force && _libraryCache) return _libraryCache;
    if (_libraryCachePromise) return _libraryCachePromise;
    _libraryCachePromise = (async () => {
      try {
        const data = await requestJson('/api/admin/image-library?enabled_only=true&limit=80');
        _libraryCache = data.ok ? (data.items || []) : [];
      } catch (e) {
        _libraryCache = [];
      }
      _libraryCachePromise = null;
      return _libraryCache;
    })();
    return _libraryCachePromise;
  }

  function invalidateCache() { _libraryCache = null; }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function thumbnailUrl(item) {
    if (item._thumb_url) return item._thumb_url;
    item._thumb_url = item.thumb_160_url || item.thumb_url || item.thumb_320_url || fallbackThumbnailUrl(item) || item.source_url || '';
    return item._thumb_url;
  }

  function fallbackThumbnailUrl(item, size) {
    if (!item || item.id == null || item.id === '') return '';
    const targetSize = size || 160;
    let url = '/api/admin/image-library/' + encodeURIComponent(String(item.id)) + '/thumbnail?size=' + targetSize;
    if (item.updated_at) url += '&v=' + encodeURIComponent(String(item.updated_at));
    return url;
  }

  function srcsetFor(item, fallbackUrl) {
    const small = item.thumb_160_url || fallbackThumbnailUrl(item, 160) || fallbackUrl;
    const medium = item.thumb_320_url || fallbackThumbnailUrl(item, 320) || fallbackUrl;
    if (!small || !medium) return '';
    return escapeHtml(small) + ' 160w, ' + escapeHtml(medium) + ' 320w';
  }

  function renderThumbImage(cell, item, size) {
    if (!cell) return;
    const firstUrl = thumbnailUrl(item);
    const fallbackUrl = fallbackThumbnailUrl(item, size || 160);
    if (!firstUrl && !fallbackUrl) return;
    const img = document.createElement('img');
    img.loading = 'lazy';
    img.decoding = 'async';
    img.width = size || 120;
    img.height = size || 120;
    img.alt = '';
    img.style.cssText = 'width:100%;height:100%;object-fit:cover;';
    const srcset = srcsetFor(item, fallbackUrl || firstUrl);
    if (srcset) {
      img.setAttribute('srcset', srcset);
      img.setAttribute('sizes', (size || 120) + 'px');
    }
    img.onerror = function () {
      if (fallbackUrl && img.src.indexOf(fallbackUrl) < 0) {
        img.removeAttribute('srcset');
        img.removeAttribute('sizes');
        img.src = fallbackUrl;
        return;
      }
      cell.textContent = '无图';
    };
    cell.textContent = '';
    img.src = firstUrl || fallbackUrl;
    cell.appendChild(img);
  }

  function showLibraryPickerModal({ existing, onConfirm, mode, max }) {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:9999;display:flex;align-items:center;justify-content:center;';
    wrap.innerHTML = `
      <div style="background:#fff;border-radius:8px;padding:18px;max-width:640px;width:92%;max-height:80vh;display:flex;flex-direction:column;">
        <header style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <strong>从图片素材库选择</strong>
          <button class="img-picker-close" type="button" style="border:0;background:transparent;font-size:22px;cursor:pointer;color:#888;">×</button>
        </header>
        <div class="img-picker-list" style="overflow:auto;flex:1;display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px;padding:4px;">加载中…</div>
        <footer style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;gap:10px;">
          <span class="img-picker-count subtle" style="color:#888;font-size:12px;"></span>
          <div>
            <a href="/admin/image-library" target="_blank" style="font-size:12px;color:#2c5cdb;margin-right:12px;">管理素材库</a>
            <button class="img-picker-cancel" type="button" style="padding:6px 14px;margin-right:6px;">取消</button>
            <button class="img-picker-confirm admin-button admin-button--primary" type="button" style="padding:6px 14px;">确定</button>
          </div>
        </footer>
      </div>
    `;
    document.body.appendChild(wrap);
    const listEl = wrap.querySelector('.img-picker-list');
    const countEl = wrap.querySelector('.img-picker-count');

    let chosen = mode === 'multiple' ? new Set(existing.map(String)) : (existing.length ? String(existing[0]) : '');

    function close() { wrap.remove(); }
    wrap.querySelector('.img-picker-close').addEventListener('click', close);
    wrap.querySelector('.img-picker-cancel').addEventListener('click', close);
    wrap.querySelector('.img-picker-confirm').addEventListener('click', () => {
      const ids = mode === 'multiple' ? Array.from(chosen) : (chosen ? [chosen] : []);
      onConfirm(ids);
      close();
    });

    fetchLibrary().then(async (items) => {
      if (!items.length) {
        listEl.innerHTML = '<div style="grid-column:1/-1;color:#888;font-size:13px;text-align:center;padding:30px;">素材库还是空的，先去 <a href="/admin/image-library" target="_blank">/admin/image-library</a> 上传一些。</div>';
        return;
      }
      // 先渲染骨架占位，再填服务端返回的 thumb_160_url，避免批量拉原图详情。
      listEl.innerHTML = items.map(function (it) {
        const itemId = String(it.id);
        const isSel = mode === 'multiple' ? chosen.has(itemId) : (chosen === itemId);
        return '<label data-id="' + it.id + '" style="border:2px solid ' + (isSel ? '#2c5cdb' : '#e5e7eb') + ';border-radius:6px;padding:6px;cursor:pointer;font-size:11px;display:flex;flex-direction:column;gap:6px;">'
          + '<div class="img-picker-thumb" style="width:100%;aspect-ratio:1;background:#f5f6f8;border-radius:4px;display:flex;align-items:center;justify-content:center;color:#bbb;overflow:hidden;">…</div>'
          + '<div style="display:flex;align-items:center;gap:4px;"><input type="' + (mode === 'multiple' ? 'checkbox' : 'radio') + '" name="img-picker" ' + (isSel ? 'checked' : '') + ' style="margin:0;"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(it.name || it.file_name || '#' + it.id) + '</span></div>'
          + '</label>';
      }).join('');
      function updateCount() {
        const n = mode === 'multiple' ? chosen.size : (chosen ? 1 : 0);
        countEl.textContent = mode === 'multiple' ? ('已选 ' + n + (max ? ' / ' + max : '')) : (n ? '已选 1' : '');
      }
      updateCount();
      // 填缩略图
      items.forEach(function (it) {
        const cell = listEl.querySelector('label[data-id="' + it.id + '"] .img-picker-thumb');
        renderThumbImage(cell, it, 120);
      });
      // 选择交互
      listEl.querySelectorAll('label[data-id]').forEach(function (lbl) {
        const id = String(lbl.dataset.id);
        lbl.addEventListener('click', function (e) {
          if (e.target.tagName === 'INPUT') return;  // 让 input 自己处理
          const input = lbl.querySelector('input');
          input.checked = !input.checked;
          input.dispatchEvent(new Event('change', { bubbles: true }));
        });
        lbl.querySelector('input').addEventListener('change', function () {
          if (mode === 'multiple') {
            if (this.checked) {
              if (max && chosen.size >= max) {
                this.checked = false;
                alert('最多只能选 ' + max + ' 张');
                return;
              }
              chosen.add(id);
            } else {
              chosen.delete(id);
            }
            lbl.style.borderColor = this.checked ? '#2c5cdb' : '#e5e7eb';
          } else {
            chosen = this.checked ? id : '';
            // 单选清掉其他
            listEl.querySelectorAll('label').forEach(function (other) {
              other.style.borderColor = (String(other.dataset.id) === chosen) ? '#2c5cdb' : '#e5e7eb';
            });
          }
          updateCount();
        });
      });
    });
  }

  /**
   * @param {HTMLElement} container 容器元素
   * @param {Object} options
   * @returns {Object} { getValue: () => number|number[], setValue: (v) => void, refresh: () => void }
   */
  window.mountImagePicker = function mountImagePicker(container, options) {
    options = options || {};
    const mode = options.mode === 'multiple' ? 'multiple' : 'single';
    const max = options.max || 9;
    let value = options.value;
    if (mode === 'multiple') value = Array.isArray(value) ? value.map(String) : [];
    else value = value ? String(value) : '';
    const onChange = typeof options.onChange === 'function' ? options.onChange : function () {};

    container.innerHTML = `
      <div class="img-picker-root">
        <div class="img-picker-selected" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px;"></div>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
          <input type="file" accept="image/png,image/jpeg" class="img-picker-upload-file" style="font-size:12px;" ${mode === 'multiple' ? 'multiple' : ''}>
          <button type="button" class="img-picker-pick admin-button admin-button--ghost" style="font-size:12px;padding:4px 12px;">从素材库选</button>
          <span class="img-picker-status" style="color:#888;font-size:12px;"></span>
        </div>
      </div>
    `;
    const selectedEl = container.querySelector('.img-picker-selected');
    const fileInput = container.querySelector('.img-picker-upload-file');
    const pickBtn = container.querySelector('.img-picker-pick');
    const statusEl = container.querySelector('.img-picker-status');

    async function renderSelected() {
      const ids = mode === 'multiple' ? value : (value ? [value] : []);
      if (!ids.length) {
        selectedEl.innerHTML = '<span style="color:#aaa;font-size:12px;">未选择图片</span>';
        return;
      }
      const items = await fetchLibrary();
      selectedEl.innerHTML = ids.map(function (id) {
        const it = items.find(function (x) { return String(x.id) === String(id); }) || { id: id, name: '#' + id };
        return '<div class="img-picker-chip" data-id="' + id + '" style="display:flex;align-items:center;gap:6px;padding:4px 8px;border:1px solid #e5e7eb;border-radius:4px;background:#fafbfc;font-size:12px;">'
          + '<div class="img-picker-chip-thumb" style="width:28px;height:28px;background:#f5f6f8;border-radius:3px;overflow:hidden;display:flex;align-items:center;justify-content:center;color:#bbb;font-size:10px;">…</div>'
          + '<span style="max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(it.name || it.file_name || ('#' + id)) + '</span>'
          + '<button type="button" data-remove="' + id + '" style="border:0;background:transparent;color:#a02929;cursor:pointer;font-size:14px;">×</button>'
          + '</div>';
      }).join('');
      // 填缩略图
      ids.forEach(function (id) {
        const it = items.find(function (x) { return String(x.id) === String(id); }) || { id: id, updated_at: '' };
        if (!it) return;
        const cell = selectedEl.querySelector('.img-picker-chip[data-id="' + id + '"] .img-picker-chip-thumb');
        renderThumbImage(cell, it, 28);
      });
      // 移除按钮
      selectedEl.querySelectorAll('[data-remove]').forEach(function (btn) {
        btn.addEventListener('click', function () {
          const rid = String(btn.dataset.remove);
          if (mode === 'multiple') {
            value = value.filter(function (x) { return String(x) !== rid; });
          } else {
            value = '';
          }
          renderSelected();
          onChange(mode === 'multiple' ? value : value);
        });
      });
    }

    pickBtn.addEventListener('click', function () {
      const ids = mode === 'multiple' ? value.map(String) : (value ? [String(value)] : []);
      showLibraryPickerModal({
        existing: ids,
        mode: mode,
        max: max,
        onConfirm: function (newIds) {
          if (mode === 'multiple') {
            value = newIds.slice(0, max);
          } else {
            value = newIds.length ? newIds[0] : '';
          }
          renderSelected();
          onChange(mode === 'multiple' ? value : value);
        },
      });
    });

    fileInput.addEventListener('change', async function () {
      const files = Array.from(fileInput.files || []);
      if (!files.length) return;
      fileInput.disabled = true;
      let ok = 0, fail = 0;
      for (let i = 0; i < files.length; i++) {
        statusEl.textContent = '处理中 ' + (i + 1) + '/' + files.length + '：' + files[i].name;
        try {
          const prepared = window.ImageUploadClient
            ? await window.ImageUploadClient.prepareImageForUpload(files[i])
            : { file: files[i], compressed: false };
          statusEl.textContent = (prepared.compressed ? '压缩后上传 ' : '上传中 ')
            + (i + 1) + '/' + files.length + '：' + files[i].name;
          const fd = new FormData();
          fd.append('image', prepared.file);
          const data = await requestJson('/api/admin/image-library/upload', {
            method: 'POST',
            credentials: 'same-origin',
            body: fd,
          });
          if (data.ok && data.item) {
            ok++;
            invalidateCache();  // 让新素材立刻显示在 picker 里
            if (mode === 'multiple') {
              if (value.length < max) value.push(data.item.id);
            } else {
              value = String(data.item.id);
            }
          } else {
            fail++;
            statusEl.textContent = '上传失败：' + (data.error || '未知错误');
          }
        } catch (e) {
          fail++;
          statusEl.textContent = '上传失败：' + String((e && e.message) || e);
        }
      }
      statusEl.textContent = '已上传 ' + ok + (fail ? '，失败 ' + fail : '') + ' 张';
      fileInput.disabled = false;
      fileInput.value = '';
      // 等下一帧让 invalidateCache 后的 fetchLibrary 重新拉
      await fetchLibrary({ force: true });
      renderSelected();
      onChange(mode === 'multiple' ? value : value);
    });

    renderSelected();

    return {
      getValue: function () { return mode === 'multiple' ? value.slice() : value; },
      setValue: function (v) {
        if (mode === 'multiple') value = Array.isArray(v) ? v.map(String) : [];
        else value = v ? String(v) : '';
        renderSelected();
      },
      refresh: function () { invalidateCache(); renderSelected(); },
    };
  };

  // 也暴露这个，让别处可以预热缓存或失效
  window.imageLibraryCache = { fetch: fetchLibrary, invalidate: invalidateCache };
})();

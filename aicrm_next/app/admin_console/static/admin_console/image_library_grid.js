(function () {
  'use strict';

  function create(options) {
    var container = options.container;
    var state = options.state;
    var escapeHtml = options.escapeHtml;
    var thumbnailUrl = options.thumbnailUrl;
    var openItem = options.openItem;

    function resetImages() {
      container.innerHTML = '';
      if (state.thumbObserver) {
        try { state.thumbObserver.disconnect(); } catch (error) {}
      }
      state.thumbObserver = null;
      if (state.imageController) {
        try { state.imageController.abort(); } catch (error) {}
      }
      state.imageController = new AbortController();
    }

    function renderThumbnailFailure(card, message, retryable) {
      var cell = card && card.querySelector('.il-thumb');
      if (!cell) return;
      if (!retryable) {
        cell.textContent = message || '预览不可用';
        return;
      }
      cell.innerHTML = '<button type="button" data-image-thumb-retry style="border:0;background:transparent;color:#6b7280;cursor:pointer;font-size:12px;">点击重试</button>';
      var retryButton = cell.querySelector('[data-image-thumb-retry]');
      retryButton.addEventListener('click', function (event) {
        event.preventDefault();
        event.stopPropagation();
        loadCardThumbnail(card);
      }, { once: true });
    }

    function loadCardThumbnail(card) {
      if (!card || card.dataset.thumbLoading === 'true') return;
      var item = state.items.find(function (candidate) { return String(candidate.id) === String(card.dataset.id); });
      if (!item) return;
      var url = thumbnailUrl(item);
      var cell = card.querySelector('.il-thumb');
      if (!cell || !url) {
        renderThumbnailFailure(card, '预览不可用', false);
        return;
      }
      card.dataset.thumbLoading = 'true';
      cell.innerHTML = '<span data-image-thumb-status>正在加载</span>';
      var status = cell.querySelector('[data-image-thumb-status]');
      var image = document.createElement('img');
      image.loading = 'lazy';
      image.decoding = 'async';
      image.fetchPriority = 'low';
      image.width = 180;
      image.height = 180;
      image.alt = '';
      image.style.opacity = '0';
      cell.appendChild(image);
      if (!window.ImageResourceLoader) {
        image.addEventListener('load', function () {
          image.style.opacity = '';
          if (status.isConnected) status.remove();
        }, { once: true });
        image.addEventListener('error', function () { renderThumbnailFailure(card, '预览不可用', false); }, { once: true });
        image.src = url;
        delete card.dataset.thumbLoading;
        return;
      }
      window.ImageResourceLoader.loadInto(image, url, {
        signal: state.imageController ? state.imageController.signal : undefined,
        cancelOutsideViewport: true,
        onState: function (nextState) {
          if (!status.isConnected) return;
          if (nextState === 'pending') status.textContent = '正在生成';
          else if (nextState === 'retrying') status.textContent = '正在重试';
        },
      }).then(function () {
        if (!card.isConnected) return;
        image.style.opacity = '';
        if (status.isConnected) status.remove();
      }).catch(function (error) {
        if (!card.isConnected) return;
        var parentAborted = Boolean(state.imageController && state.imageController.signal.aborted);
        if (error && error.name === 'AbortError') {
          if (error.reason === 'outside_viewport' && !parentAborted && state.thumbObserver) {
            cell.textContent = '等待加载';
            delete card.dataset.thumbLoading;
            state.thumbObserver.observe(card);
          }
          return;
        }
        renderThumbnailFailure(card, error && error.retryable ? '点击重试' : '预览不可用', Boolean(error && error.retryable));
      }).finally(function () {
        delete card.dataset.thumbLoading;
      });
    }

    function render(items, append) {
      if (!append) resetImages();
      if (!items.length && !state.items.length) {
        container.innerHTML = '<p class="il-empty-grid">没有匹配的素材。可以试试重置筛选条件，或上传一张新图。</p>';
        return;
      }

      var html = items.map(function (item) {
        var chips = [];
        if (item.category) chips.push('<span class="cat">' + escapeHtml(item.category) + '</span>');
        var tags = item.tags || [];
        tags.slice(0, 4).forEach(function (tag) {
          chips.push('<span class="tag">' + escapeHtml(tag) + '</span>');
        });
        if (tags.length > 4) chips.push('<span class="tag-more">+' + (tags.length - 4) + '</span>');
        if (!item.description && !tags.length && !item.category) chips.push('<span class="unlabeled">未打标</span>');
        var disabledClass = item.enabled ? '' : ' disabled';
        var name = escapeHtml(item.name || item.file_name || ('#' + item.id));
        return '<div class="il-card' + disabledClass + '" data-id="' + item.id + '" data-page-unbound="true" title="' + name + '">'
          + '<div class="il-thumb">…</div><div class="il-card-body"><div class="il-card-chips">'
          + (chips.length ? chips.join('') : '<span style="color:#bbb;font-size:11px;">（无分类无标签）</span>')
          + '</div></div></div>';
      }).join('');
      if (html) {
        var sentinel = state.pager && state.pager.sentinel;
        if (sentinel && sentinel.parentNode === container) sentinel.insertAdjacentHTML('beforebegin', html);
        else container.insertAdjacentHTML('beforeend', html);
      }

      if (!state.thumbObserver && typeof IntersectionObserver !== 'undefined') {
        state.thumbObserver = new IntersectionObserver(function (entries) {
          entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            var card = entry.target;
            state.thumbObserver.unobserve(card);
            loadCardThumbnail(card);
          });
        }, { rootMargin: '60px 0px' });
      }

      container.querySelectorAll('.il-card[data-page-unbound="true"]').forEach(function (card) {
        card.removeAttribute('data-page-unbound');
        if (state.thumbObserver) state.thumbObserver.observe(card);
        else loadCardThumbnail(card);
        card.addEventListener('click', function () {
          var item = state.items.find(function (candidate) { return String(candidate.id) === String(card.dataset.id); });
          if (item) openItem(item);
        });
      });
    }

    return { render: render, reset: resetImages };
  }

  window.ImageLibraryGrid = { create: create };
})();

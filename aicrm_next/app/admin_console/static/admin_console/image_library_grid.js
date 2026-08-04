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

      if (!state.thumbObserver) {
        state.thumbObserver = new IntersectionObserver(function (entries) {
          entries.forEach(function (entry) {
            if (!entry.isIntersecting) return;
            var card = entry.target;
            var item = state.items.find(function (candidate) { return String(candidate.id) === String(card.dataset.id); });
            state.thumbObserver.unobserve(card);
            if (!item) return;
            var url = thumbnailUrl(item);
            var cell = card.querySelector('.il-thumb');
            if (!cell || !url) return;
            var image = document.createElement('img');
            image.loading = 'lazy';
            image.decoding = 'async';
            image.fetchPriority = 'low';
            image.width = 180;
            image.height = 180;
            image.alt = '';
            cell.textContent = '';
            cell.appendChild(image);
            window.ImageResourceLoader.loadInto(image, url, {
              signal: state.imageController ? state.imageController.signal : undefined,
              cancelOutsideViewport: true,
            }).catch(function (error) {
              if (!(error && error.name === 'AbortError')) cell.textContent = '稍后重试';
            });
          });
        }, { rootMargin: '60px 0px' });
      }

      container.querySelectorAll('.il-card[data-page-unbound="true"]').forEach(function (card) {
        card.removeAttribute('data-page-unbound');
        state.thumbObserver.observe(card);
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

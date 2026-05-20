document.addEventListener('DOMContentLoaded', function () {

  // Confirm dialogs — remplace onsubmit="return confirm(...)"
  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      if (!confirm(form.getAttribute('data-confirm'))) e.preventDefault();
    });
  });

  // Broken favicons / thumbnails
  document.querySelectorAll('img[data-hide-on-error]').forEach(function (img) {
    img.addEventListener('error', function () {
      var action = img.getAttribute('data-hide-on-error');
      if (action === 'parent') img.parentElement.style.display = 'none';
      else if (action === 'self') img.style.display = 'none';
      else img.style.visibility = 'hidden';
    });
  });

  // Révéler la clé API
  var revealBtn = document.getElementById('reveal-apikey-btn');
  if (revealBtn) {
    revealBtn.addEventListener('click', function () {
      var target = document.getElementById(revealBtn.getAttribute('data-reveal'));
      if (target) {
        target.textContent = revealBtn.getAttribute('data-value');
        revealBtn.style.display = 'none';
      }
    });
  }

  // Copier la clé API
  var copyBtn = document.getElementById('copy-apikey-btn');
  if (copyBtn) {
    copyBtn.addEventListener('click', function () {
      navigator.clipboard.writeText(copyBtn.getAttribute('data-value'))
        .then(function () { copyBtn.textContent = 'Copié !'; });
    });
  }


  // Drag & Drop réorganisation dossiers sidebar
  (function () {
    var root = document.getElementById('sidebar-folder-root');
    if (!root || typeof Sortable === 'undefined') return;

    var items = Array.from(root.querySelectorAll('.sidebar-folder-item'));
    if (!items.length) return;

    var map = {};
    items.forEach(function (el) {
      map[el.dataset.folderId] = { el: el, parentId: el.dataset.parentId || '', children: [] };
    });

    var roots = [];
    Object.values(map).forEach(function (node) {
      if (node.parentId && map[node.parentId]) {
        map[node.parentId].children.push(node);
      } else {
        roots.push(node);
      }
    });

    function getCsrfToken() {
      var el = document.querySelector('input[name="csrf_token"]');
      return el ? el.value : '';
    }

    function isDescendant(targetContainer, draggedId) {
      var el = targetContainer;
      while (el) {
        var node = el.closest('.folder-node');
        if (!node) break;
        if (node.dataset.folderId === draggedId) return true;
        el = node.parentElement;
      }
      return false;
    }

    function serialize(container, parentId) {
      var result = [];
      Array.from(container.children).forEach(function (node, idx) {
        var fid = node.dataset.folderId;
        if (!fid) return;
        result.push({ id: parseInt(fid), parent_id: parentId, sort_order: idx });
        var child = node.querySelector(':scope > .folder-children');
        if (child) result.push.apply(result, serialize(child, parseInt(fid)));
      });
      return result;
    }

    var rootContainer;

    var opts = {
      group: { name: 'sidebar-folders', pull: true, put: true },
      animation: 150,
      handle: '.drag-handle',
      fallbackOnBody: true,
      swapThreshold: 0.65,
      onMove: function (evt) {
        if (isDescendant(evt.to, evt.dragged.dataset.folderId)) return false;
      },
      onEnd: function (evt) {
        if (evt.from === evt.to && evt.oldIndex === evt.newIndex) return;
        var payload = serialize(rootContainer, null);
        fetch('/folders/reorder', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
          body: JSON.stringify(payload),
        }).then(function (r) { if (r.ok) location.reload(); });
      },
    };

    function buildDOM(nodes, container) {
      nodes.forEach(function (node) {
        var wrapper = document.createElement('div');
        wrapper.className = 'folder-node';
        wrapper.dataset.folderId = node.el.dataset.folderId;
        node.el.style.paddingLeft = '';
        wrapper.appendChild(node.el);
        var childContainer = document.createElement('div');
        childContainer.className = 'folder-children';
        childContainer.dataset.parentId = node.el.dataset.folderId;
        if (node.children.length) buildDOM(node.children, childContainer);
        wrapper.appendChild(childContainer);
        container.appendChild(wrapper);
        Sortable.create(childContainer, opts);
      });
    }

    rootContainer = document.createElement('div');
    rootContainer.className = 'folder-children';
    rootContainer.dataset.parentId = '';
    root.innerHTML = '';
    buildDOM(roots, rootContainer);
    root.appendChild(rootContainer);
    Sortable.create(rootContainer, opts);
  })();
});

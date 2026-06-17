document.addEventListener('DOMContentLoaded', function () {

  // Toggle vue liste / grille
  var VIEW_KEY = 'excerpta-view';
  function applyView(v) {
    var list = document.querySelector('.links-list');
    if (!list) return;
    list.classList.toggle('view-grid', v === 'grid');
    var btn = document.getElementById('view-toggle-btn');
    if (!btn) return;
    btn.querySelector('.icon-grid').style.display = v === 'grid' ? 'none' : '';
    btn.querySelector('.icon-list').style.display = v === 'grid' ? '' : 'none';
    btn.title = v === 'grid' ? 'Vue liste' : 'Vue grille';
  }
  var currentView = localStorage.getItem(VIEW_KEY) || 'list';
  applyView(currentView);
  var viewBtn = document.getElementById('view-toggle-btn');
  if (viewBtn) {
    viewBtn.addEventListener('click', function() {
      currentView = currentView === 'list' ? 'grid' : 'list';
      localStorage.setItem(VIEW_KEY, currentView);
      applyView(currentView);
    });
  }

  // Raccourcis clavier globaux
  document.addEventListener('keydown', function (e) {
    var tag = (document.activeElement || {}).tagName || '';
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === 'n') { e.preventDefault(); window.location.href = '/links/add'; }
    if (e.key === '/') {
      e.preventDefault();
      var s = document.querySelector('input[name="q"]');
      if (s) { s.focus(); s.select(); }
    }
  });

  // Undo toast — remplace les confirm() de suppression
  function _showUndoToast(msg, onConfirm, onUndo) {
    var existing = document.getElementById('undo-toast');
    if (existing) {
      // Un nouveau toast remplace l'ancien : on VALIDE l'action en attente
      // (au lieu de l'annuler silencieusement) pour ne pas perdre des
      // suppressions enchaînées rapidement. Seule la dernière reste annulable.
      clearTimeout(existing._timer);
      if (existing._onConfirm) { existing._onConfirm(); existing._onConfirm = null; }
      existing.remove();
    }
    var toast = document.createElement('div');
    toast.id = 'undo-toast';
    toast.className = 'undo-toast';
    toast.innerHTML = msg + ' <button class="undo-toast-btn" type="button">Annuler</button>';
    document.body.appendChild(toast);
    toast._onConfirm = onConfirm;
    var timer = setTimeout(function() {
      toast._onConfirm = null; toast.remove(); onConfirm();
    }, 5000);
    toast._timer = timer;
    toast.querySelector('.undo-toast-btn').addEventListener('click', function() {
      clearTimeout(timer); toast._onConfirm = null; toast.remove(); if (onUndo) onUndo();
    });
  }
  window._showUndoToast = _showUndoToast; // réutilisable depuis les composants Alpine

  document.querySelectorAll('form[data-confirm]').forEach(function(form) {
    var fired = false;
    form.addEventListener('submit', function(e) {
      if (fired) return;
      e.preventDefault();
      if (!window.confirm(form.getAttribute('data-confirm') || 'Supprimer ?')) return;
      var card = form.closest('.link-card');
      if (!card) { fired = true; form.submit(); return; } // page édition / check_links : redirect classique
      // Suppression immédiate en AJAX : on retire la carte sans recharger la page
      card.classList.add('link-deleting');
      var csrf = form.querySelector('input[name="csrf_token"]');
      fetch(form.action, {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf ? csrf.value : '' },
        body: new FormData(form),
      }).then(function(r) {
        if (r.ok) card.remove();
        else card.classList.remove('link-deleting');
      }).catch(function() { card.classList.remove('link-deleting'); });
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

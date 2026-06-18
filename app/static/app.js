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

  // Délégation : survit aux remplacements AJAX de #results (pagination, recherche).
  // Lier par élément cassait la suppression sur les pages chargées en AJAX.
  document.addEventListener('submit', function(e) {
    var form = e.target;
    if (!form || !form.matches || !form.matches('form[data-confirm]')) return;
    if (form._confirmed) return;
    e.preventDefault();
    if (!window.confirm(form.getAttribute('data-confirm') || 'Supprimer ?')) return;
    var card = form.closest('.link-card');
    if (!card) { form._confirmed = true; form.submit(); return; } // page édition / check_links : redirect classique
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


  // Tri alphabétique ponctuel des dossiers
  (function () {
    var btn = document.getElementById('folder-sort-alpha');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var csrf = document.querySelector('input[name="csrf_token"]');
      btn.disabled = true;
      fetch('/folders/sort-alpha', {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf ? csrf.value : '' },
      }).then(function (r) {
        if (r.ok) location.reload();
        else btn.disabled = false;
      }).catch(function () { btn.disabled = false; });
    });
  })();

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

    // Itérer dans l'ordre du DOM (= ordre serveur par sort_order), PAS
    // Object.values(map) qui réordonnerait par folderId (clés entières → tri
    // numérique JS), ce qui écraserait le tri alpha / le drag&drop manuel.
    var roots = [];
    items.forEach(function (el) {
      var node = map[el.dataset.folderId];
      if (node.parentId && map[node.parentId]) {
        map[node.parentId].children.push(node);
      } else {
        roots.push(node);
      }
    });

    // État plié/déplié persistant (ids de dossiers repliés)
    var COLLAPSE_KEY = 'excerpta-folders-collapsed';
    var collapsed;
    try { collapsed = new Set(JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '[]')); }
    catch (e) { collapsed = new Set(); }
    function saveCollapsed() {
      localStorage.setItem(COLLAPSE_KEY, JSON.stringify(Array.from(collapsed)));
    }

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
        if (node.children.length) {
          buildDOM(node.children, childContainer);
          // Chevron plier/déplier (uniquement sur les dossiers à enfants)
          var fid = node.el.dataset.folderId;
          var toggle = document.createElement('button');
          toggle.type = 'button';
          toggle.className = 'folder-toggle';
          toggle.setAttribute('aria-label', 'Plier ou déplier');
          toggle.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';
          if (collapsed.has(fid)) wrapper.classList.add('collapsed');
          toggle.setAttribute('aria-expanded', collapsed.has(fid) ? 'false' : 'true');
          toggle.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            var isCollapsed = wrapper.classList.toggle('collapsed');
            if (isCollapsed) collapsed.add(fid); else collapsed.delete(fid);
            toggle.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
            saveCollapsed();
          });
          node.el.insertBefore(toggle, node.el.firstChild);
        }
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

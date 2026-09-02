var THEME_PAIRS = {
  'light': 'dark', 'dark': 'light',
  'nord': 'nord-dark', 'nord-dark': 'nord',
  'dracula': 'dracula-light', 'dracula-light': 'dracula',
  'catppuccin': 'catppuccin-latte', 'catppuccin-latte': 'catppuccin',
  'gruvbox': 'gruvbox-light', 'gruvbox-light': 'gruvbox',
  'solarized': 'solarized-dark', 'solarized-dark': 'solarized',
  'rosepine': 'rosepine-dawn', 'rosepine-dawn': 'rosepine',
};

var DARK_THEMES = ['dark', 'nord-dark', 'dracula', 'catppuccin', 'gruvbox', 'solarized-dark', 'rosepine'];

function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  localStorage.setItem('theme', t);
  var label = document.getElementById('theme-label');
  // Gabarit traduit porté par l'élément (data-template), le JS n'ayant pas
  // accès à gettext.
  if (label) label.textContent = (label.dataset.template || 'Active theme: {name}').replace('{name}', t);
  _updateToggleIcon(t);
}

function toggleDarkLight() {
  var t = localStorage.getItem('theme') || 'light';
  var pair = THEME_PAIRS[t];
  if (pair) applyTheme(pair);
}

function _updateToggleIcon(t) {
  var btn = document.getElementById('theme-toggle');
  if (!btn) return;
  btn.title = DARK_THEMES.indexOf(t) >= 0 ? 'Passer en clair' : 'Passer en sombre';
  btn.querySelector('.icon-sun').style.display = DARK_THEMES.indexOf(t) >= 0 ? '' : 'none';
  btn.querySelector('.icon-moon').style.display = DARK_THEMES.indexOf(t) >= 0 ? 'none' : '';
}

document.addEventListener('DOMContentLoaded', function () {
  var t = localStorage.getItem('theme') || 'light';
  var sel = document.getElementById('theme-select');
  if (sel) sel.value = t;
  var label = document.getElementById('theme-label');
  // Gabarit traduit porté par l'élément (data-template), le JS n'ayant pas
  // accès à gettext.
  if (label) label.textContent = (label.dataset.template || 'Active theme: {name}').replace('{name}', t);
  _updateToggleIcon(t);

  document.querySelectorAll('[data-theme-apply]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      applyTheme(btn.getAttribute('data-theme-apply'));
    });
  });

  document.querySelectorAll('select.theme-select').forEach(function (sel) {
    sel.addEventListener('change', function () {
      applyTheme(sel.value);
    });
  });

  var toggle = document.getElementById('theme-toggle');
  if (toggle) toggle.addEventListener('click', toggleDarkLight);
});

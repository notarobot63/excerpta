function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  localStorage.setItem('theme', t);
  var label = document.getElementById('theme-label');
  if (label) label.textContent = 'Thème actif : ' + t;
}

document.addEventListener('DOMContentLoaded', function () {
  var t = localStorage.getItem('theme') || 'light';
  var sel = document.getElementById('theme-select');
  if (sel) sel.value = t;
  var label = document.getElementById('theme-label');
  if (label) label.textContent = 'Thème actif : ' + t;

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
});

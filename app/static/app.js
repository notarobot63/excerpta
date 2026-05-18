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

});

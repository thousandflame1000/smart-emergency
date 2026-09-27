/* 操作手感：按下的按鈕在請求完成前顯示處理中且擋掉連點；訊息改成不擋畫面的提示；
   後台彈窗支援 Enter 送出、Esc 關閉。各頁照常呼叫 fetch，不需要改寫。 */
(function () {
  var pressed = null, pressedAt = 0;
  function mark(event) {
    var target = event.target && event.target.closest && event.target.closest('button, .btn, [role="button"]');
    if (target) { pressed = target; pressedAt = Date.now(); }
  }
  document.addEventListener('pointerdown', mark, true);
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' || event.key === ' ') mark(event);
  }, true);

  // 按下後 1.2 秒內發出的請求算在那顆按鈕上：顯示處理中，完成前不能再按。
  var nativeFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var button = pressed && Date.now() - pressedAt < 1200 && document.contains(pressed) ? pressed : null;
    var request = nativeFetch(input, init);
    if (button) {
      button.__feelBusy = (button.__feelBusy || 0) + 1;
      button.classList.add('is-busy');
      button.setAttribute('aria-busy', 'true');
      var done = function () {
        button.__feelBusy -= 1;
        if (button.__feelBusy <= 0) { button.classList.remove('is-busy'); button.removeAttribute('aria-busy'); }
      };
      request.then(done, done);
    }
    return request;
  };

  // 不擋畫面的提示，取代原生 alert()。
  window.feelToast = function (message, kind) {
    var stack = document.querySelector('.feel-toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'feel-toast-stack';
      stack.setAttribute('role', 'status');
      stack.setAttribute('aria-live', 'polite');
      document.body.appendChild(stack);
    }
    var item = document.createElement('div');
    item.className = 'feel-toast' + (kind === 'danger' ? ' danger' : '');
    item.textContent = String(message);
    stack.appendChild(item);
    requestAnimationFrame(function () { item.classList.add('show'); });
    var ms = Math.min(9000, 2600 + String(message).length * 45);
    setTimeout(function () {
      item.classList.remove('show');
      setTimeout(function () { item.remove(); }, 220);
    }, ms);
  };

  // 後台彈窗（.modal-overlay.open）：Enter 按主要按鈕、Esc 關閉。
  // 中文輸入法選字也會按 Enter，選字中（isComposing）一律不動作。
  document.addEventListener('keydown', function (event) {
    if (event.isComposing || event.keyCode === 229) return;
    var open = document.querySelectorAll('.modal-overlay.open');
    var modal = open[open.length - 1];
    if (!modal) return;
    if (event.key === 'Escape') {
      var close = modal.querySelector('#confirm-cancel-btn, .modal-close');
      if (close) { event.preventDefault(); close.click(); }
    } else if (event.key === 'Enter' && !event.shiftKey) {
      var field = event.target;
      if (!field || field.tagName !== 'INPUT' || !modal.contains(field)) return;
      var buttons = modal.querySelectorAll('.modal-footer .btn:not(:disabled)');
      var primary = buttons[buttons.length - 1];
      if (primary) { event.preventDefault(); primary.click(); }
    }
  });
})();

/* 新的一鍵求救進來時：紅色橫幅（要有人按才消失）＋三聲警示音＋分頁標題閃爍。
   外框（site.html）每 30 秒輪詢總覽，單獨開啟的工作區則在每次更新現況時檢查；
   嵌在外框裡的工作區不另外響，避免同一筆響兩次。

   sosAlarm.check(list, onView)：list 是目前未處理的求救 [{id, name, address, reported_at}]。
   第一次呼叫只記下現有的，若有未處理的就安靜顯示橫幅（不響鈴）；之後出現新的 id 才響。
   sosAlarm.before 設成某個元素時，橫幅插在它前面、把內容往下推（外框用）；
   沒設就浮在畫面底部，不蓋住上方的選單與按鈕。 */
(function () {
  var known = null, banner = null, flashTimer = null, baseTitle = document.title;
  var ALARM_TITLE = '🚨 新的緊急求救';

  function since(iso) {
    if (!iso) return '';
    var m = Math.max(0, Math.floor((Date.now() - new Date(iso)) / 60000));
    return m < 1 ? '剛剛' : m < 60 ? m + ' 分鐘前' : m < 1440 ? Math.floor(m / 60) + ' 小時前' : Math.floor(m / 1440) + ' 天前';
  }

  function beep() {
    try {
      var C = window.AudioContext || window.webkitAudioContext;
      if (!C) return;
      var ctx = beep.ctx || (beep.ctx = new C());
      if (ctx.state === 'suspended') ctx.resume();
      [0, 0.35, 0.7].forEach(function (t) {
        var o = ctx.createOscillator(), g = ctx.createGain(), at = ctx.currentTime + t;
        o.type = 'square'; o.frequency.value = 880;
        g.gain.setValueAtTime(0.0001, at);
        g.gain.exponentialRampToValueAtTime(0.2, at + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, at + 0.25);
        o.connect(g); g.connect(ctx.destination); o.start(at); o.stop(at + 0.3);
      });
    } catch (e) { /* 沒有聲音時還有橫幅 */ }
  }

  function flash(on) {
    clearInterval(flashTimer); flashTimer = null;
    if (document.title === ALARM_TITLE) document.title = baseTitle;
    if (!on) return;
    var lit = false;
    flashTimer = setInterval(function () {
      if (document.title !== ALARM_TITLE) baseTitle = document.title;
      lit = !lit; document.title = lit ? ALARM_TITLE : baseTitle;
    }, 1000);
  }

  function style() {
    if (document.getElementById('sos-alarm-style')) return;
    var s = document.createElement('style'); s.id = 'sos-alarm-style';
    s.textContent = '.sos-alarm{position:fixed;bottom:40px;left:50%;transform:translateX(-50%);z-index:9999;display:flex;align-items:center;gap:12px;' +
      'max-width:calc(100vw - 32px);padding:12px 14px 12px 18px;border-radius:10px;background:#b3261e;color:#fff;font:600 15px/1.4 system-ui,"Microsoft JhengHei",sans-serif;' +
      'box-shadow:0 10px 30px rgba(0,0,0,.28)}.sos-alarm.new{animation:sos-pulse 1s ease-in-out 6}' +
      '.sos-alarm .txt{flex:1;min-width:0}.sos-alarm small{display:block;font-weight:400;opacity:.9}' +
      '.sos-alarm button{flex:none;border:0;border-radius:8px;padding:8px 14px;font:inherit;cursor:pointer}' +
      '.sos-alarm .view{background:#fff;color:#b3261e}.sos-alarm .dismiss{background:rgba(255,255,255,.18);color:#fff}' +
      '@keyframes sos-pulse{50%{box-shadow:0 0 0 8px rgba(179,38,30,.35),0 10px 30px rgba(0,0,0,.28)}}' +
      '.sos-alarm.inline{position:static;transform:none;flex:none;max-width:none;border-radius:0;box-shadow:none;padding:10px 16px}' +
      '.sos-alarm.inline.new{animation:sos-flash 1s ease-in-out 6}@keyframes sos-flash{50%{background:#e0443a}}';
    document.head.appendChild(s);
  }

  function hide() { if (banner) banner.remove(); banner = null; flash(false); }

  function show(items, fresh, onView) {
    style(); hide();
    var first = items[0];
    banner = document.createElement('div');
    var before = window.sosAlarm.before;
    banner.className = 'sos-alarm' + (before ? ' inline' : '') + (fresh ? ' new' : ''); banner.setAttribute('role', 'alert');
    var txt = document.createElement('div'); txt.className = 'txt';
    txt.textContent = (fresh ? '🚨 新的緊急求救' : '🚨 尚有緊急求救未處理') + (items.length > 1 ? ' ' + items.length + ' 筆' : '') + '：' +
      items.map(function (i) { return i.name; }).join('、');
    var sub = document.createElement('small');
    sub.textContent = [first.address, '通報 ' + since(first.reported_at)].filter(Boolean).join(' · ');
    txt.appendChild(sub);
    var view = document.createElement('button'); view.type = 'button'; view.className = 'view'; view.textContent = '查看';
    view.onclick = function () { askPermission(); hide(); onView(first); };
    var dismiss = document.createElement('button'); dismiss.type = 'button'; dismiss.className = 'dismiss'; dismiss.textContent = '知道了';
    dismiss.onclick = function () { askPermission(); hide(); };
    banner.append(txt, view, dismiss);
    if (before) before.parentNode.insertBefore(banner, before); else document.body.appendChild(banner);
    if (fresh) { beep(); flash(true); desktop(items, first, onView); }
  }

  // 後台分頁在背景（調度者在用別的程式）時，瀏覽器會延後計時器，紅條也看不到：跳桌面通知。
  // 瀏覽器規定要使用者按過東西才能問權限，所以在第一次按「查看／知道了」時順便問。
  function askPermission() {
    try { if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission(); } catch (e) { /* 不支援就算了 */ }
  }
  function desktop(items, first, onView) {
    try {
      if (!document.hidden || !('Notification' in window) || Notification.permission !== 'granted') return;
      var n = new Notification('🚨 新的緊急求救：' + items.map(function (i) { return i.name; }).join('、'),
        { body: [first.address, '通報 ' + since(first.reported_at)].filter(Boolean).join(' · '), tag: 'sos', requireInteraction: true });
      n.onclick = function () { window.focus(); hide(); onView(first); n.close(); };
    } catch (e) { /* 手機瀏覽器等不支援時，紅條與聲音照常 */ }
  }

  window.sosAlarm = {
    before: null,
    since: since,
    check: function (list, onView) {
      list = list || [];
      var ids = list.map(function (i) { return i.id; });
      if (known === null) {
        known = new Set(ids);
        if (list.length) show(list, false, onView);
        return;
      }
      var added = list.filter(function (i) { return !known.has(i.id); });
      known = new Set(ids);
      if (added.length) show(added, true, onView);
      else if (!list.length) hide();
    }
  };
})();

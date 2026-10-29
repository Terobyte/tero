// relay/public/source.js — телефонная подача для карточки HUD (спек пульта 2, п. 6).
// Классический скрипт в <head> копии view.html: до скрипта карточки ставит
// data-host="phone" и window.teroSource, так что карточка не ждёт pywebview.
// Сами запросы, вопрос, действия, привязка и пуши — в pult.js (модуль),
// который этот файл подгружает; до его прихода view() отдаёт null.
(function () {
  document.documentElement.setAttribute('data-host', 'phone');
  var latest = null;
  window.teroSource = {
    // карточка зовёт это раз в секунду; pult.js кладёт сюда готовый view
    view: function () { return latest; },
    set: function (view) { latest = view || null; },
  };
  function loadPult() {
    var s = document.createElement('script');
    s.type = 'module';
    s.src = 'pult.js';
    document.head.appendChild(s);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', loadPult);
  else loadPult();
})();

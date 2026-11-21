// chicken24.uz (sayt) ochilsa - Mini App emas, sayt sahifasiga o'tkazadi. Mini App (pages.dev) o'zgarmaydi.
(function () {
  if (/^(www\.)?chicken24\.uz$/i.test(location.hostname) && location.pathname === '/') {
    location.replace('/site/' + location.search + location.hash);
  }
})();

// =============================================================
// Универсальный сканер QR-кодов с префиксами.
// Поддерживает: M47 (материал), T12 (инструмент), K3 (ключ).
// Режимы:
//   "search"    — /take: найти карточку .item-card по M{id}
//   "admin"     — /admin/stock: найти .admin-item по M{id}
//   "inventory" — /admin/inventory: подсветить, фокус на факт
//   "tools"     — /tools: найти .tool-card по T{id}, или редирект
// =============================================================
(function () {
  let scanner = null;
  let currentCallback = null;

  function resetFilters() {
    const searchInput = document.getElementById("search-input")
                     || document.querySelector('input[name="q"]');
    if (searchInput && searchInput.value) {
      searchInput.value = "";
      searchInput.dispatchEvent(new Event("input", { bubbles: true }));
    }
    document.querySelectorAll(".item-card.hidden").forEach(function (c) {
      c.classList.remove("hidden");
    });
    const empty = document.getElementById("empty-state");
    if (empty) empty.style.display = "none";
  }

  function ensureContainer() {
    let el = document.getElementById("qr-reader-container");
    if (el) return el;

    el = document.createElement("div");
    el.id = "qr-reader-overlay";
    el.className = "qr-overlay";
    el.style.display = "none";
    el.innerHTML = [
      '<div class="qr-modal">',
      '  <div class="qr-head">',
      '    <div>📷 Сканирование QR</div>',
      '    <button type="button" class="qr-close" id="qr-close-btn">✕</button>',
      '  </div>',
      '  <div id="qr-reader-container" class="qr-reader-container"></div>',
      '  <div class="qr-hint" id="qr-hint">Наведите камеру на QR-код</div>',
      '</div>',
    ].join("");
    document.body.appendChild(el);

    document.getElementById("qr-close-btn").addEventListener("click", stopScan);
    el.addEventListener("click", function (e) {
      if (e.target === el) stopScan();
    });

    return el.querySelector("#qr-reader-container");
  }

  function startScan(onResult) {
    if (typeof Html5Qrcode === "undefined") {
      alert("Библиотека сканирования не загружена. Проверьте интернет.");
      return;
    }
    if (scanner) return;

    resetFilters();
    ensureContainer();
    document.getElementById("qr-reader-overlay").style.display = "flex";
    document.getElementById("qr-hint").textContent = "Наведите камеру на QR-код";

    currentCallback = onResult;
    scanner = new Html5Qrcode("qr-reader-container");

    scanner.start(
      { facingMode: "environment" },
      { fps: 10, qrbox: { width: 240, height: 240 }, aspectRatio: 1.0 },
      function (decodedText) {
        if (navigator.vibrate) navigator.vibrate(50);
        document.getElementById("qr-hint").textContent = "Код: " + decodedText;
        const cb = currentCallback;
        setTimeout(function () {
          stopScan();
          if (cb) cb(decodedText.trim());
        }, 250);
      },
      function () {}
    ).catch(function (err) {
      console.error("Camera error:", err);
      document.getElementById("qr-hint").textContent =
        "Ошибка камеры. Разрешите доступ в настройках браузера.";
    });
  }

  function stopScan() {
    const overlay = document.getElementById("qr-reader-overlay");
    if (overlay) overlay.style.display = "none";
    if (scanner) {
      scanner.stop().then(function () {
        scanner.clear();
        scanner = null;
      }).catch(function () {
        scanner = null;
      });
    }
    currentCallback = null;
  }

  function highlight(el, ms) {
    if (!el) return;
    el.classList.remove("qr-flash");
    void el.offsetWidth;
    el.classList.add("qr-flash");
    setTimeout(function () { el.classList.remove("qr-flash"); }, ms || 2400);
  }

  function parseCode(text) {
    // Возвращает {type, id} или null
    const code = (text || "").trim().toUpperCase();
    if (!code) return null;

    if (code.startsWith("M")) {
      const n = parseInt(code.slice(1), 10);
      if (!isNaN(n)) return { type: "material", id: n };
    }
    if (code.startsWith("T")) {
      const n = parseInt(code.slice(1), 10);
      if (!isNaN(n)) return { type: "tool", id: n };
    }
    if (code.startsWith("K")) {
      const n = parseInt(code.slice(1), 10);
      if (!isNaN(n)) return { type: "key", id: n };
    }
    // Старый формат — просто число
    const n = parseInt(code, 10);
    if (!isNaN(n)) return { type: "legacy", id: n };
    return null;
  }

  function showToolModal(toolId) {
    // Модалка «Это инструмент», с кнопкой перехода
    let el = document.getElementById("tool-hint-overlay");
    if (!el) {
      el = document.createElement("div");
      el.id = "tool-hint-overlay";
      el.className = "modal-overlay";
      el.style.display = "none";
      el.innerHTML = [
        '<div class="modal" style="max-width:360px;">',
        '  <div class="modal-head">',
        '    <div>🔧 Это инструмент</div>',
        '    <button type="button" class="modal-close" id="tool-hint-close">✕</button>',
        '  </div>',
        '  <p>Отсканированный код относится к инструменту.</p>',
        '  <div class="modal-actions">',
        '    <a href="#" class="btn btn-primary" id="tool-hint-link">Перейти к инструменту</a>',
        '    <button type="button" class="btn" id="tool-hint-cancel">Отмена</button>',
        '  </div>',
        '</div>',
      ].join("");
      document.body.appendChild(el);
      document.getElementById("tool-hint-close").addEventListener("click", function () {
        el.style.display = "none";
      });
      document.getElementById("tool-hint-cancel").addEventListener("click", function () {
        el.style.display = "none";
      });
      el.addEventListener("click", function (e) {
        if (e.target === el) el.style.display = "none";
      });
    }
    document.getElementById("tool-hint-link").href = "/tools/" + toolId;
    el.style.display = "flex";
  }

  function showLegacyModal() {
    alert("Устаревшая этикетка. Перепечатайте её из раздела Каталог.");
  }

  // -------- ПУБЛИЧНОЕ API --------

  window.openQrScanner = function (mode) {

    // ==== TAKE: M{id} → карточка ====
    if (mode === "search") {
      startScan(function (text) {
        const parsed = parseCode(text);
        if (!parsed) { alert("Неизвестный код"); return; }
        if (parsed.type === "tool") { showToolModal(parsed.id); return; }
        if (parsed.type === "legacy") { showLegacyModal(); return; }

        // material или key
        const card = document.querySelector('.item-card[data-id="' + parsed.id + '"]');
        if (!card) { alert("Позиция #" + parsed.id + " не найдена на этом складе."); return; }
        card.classList.remove("hidden");
        card.scrollIntoView({ behavior: "smooth", block: "center" });
        highlight(card, 2400);
        const qtyInput = card.querySelector(".qty-input");
        if (qtyInput) setTimeout(function () { qtyInput.focus(); }, 400);
      });
      return;
    }

    // ==== ADMIN STOCK: M{id} → карточка ====
    if (mode === "admin") {
      startScan(function (text) {
        const parsed = parseCode(text);
        if (!parsed) { alert("Неизвестный код"); return; }
        if (parsed.type === "tool") { showToolModal(parsed.id); return; }
        if (parsed.type === "legacy") { showLegacyModal(); return; }

        const item = document.querySelector('.admin-item[data-catalog-id="' + parsed.id + '"]');
        if (!item) { alert("Позиция #" + parsed.id + " не найдена."); return; }
        item.scrollIntoView({ behavior: "smooth", block: "center" });
        highlight(item, 2400);
      });
      return;
    }

    // ==== INVENTORY: M{id} → факт ====
    if (mode === "inventory") {
      startScan(function (text) {
        const parsed = parseCode(text);
        if (!parsed) { alert("Неизвестный код"); return; }
        if (parsed.type === "tool") { showToolModal(parsed.id); return; }
        if (parsed.type === "legacy") { showLegacyModal(); return; }

        const inp = document.querySelector('.inv-card-input input[name="fact_' + parsed.id + '"]');
        if (!inp) { alert("Позиция #" + parsed.id + " не найдена."); return; }
        const card = inp.closest(".inv-card");
        if (card) {
          card.scrollIntoView({ behavior: "smooth", block: "center" });
          highlight(card, 2400);
        }
        setTimeout(function () { inp.focus(); }, 400);
      });
      return;
    }

    // ==== TOOLS: T{id} → карточка инструмента ====
    if (mode === "tools") {
      startScan(function (text) {
        const parsed = parseCode(text);
        if (!parsed) { alert("Неизвестный код"); return; }

        if (parsed.type === "material") {
          // Это материал, но мы в разделе инструментов
          showToolModal(parsed.id);  // покажем как «это материал»
          // Но модалка говорит «инструмент». Переделаем.
          const overlay = document.getElementById("tool-hint-overlay");
          if (overlay) {
            overlay.querySelector(".modal-head > div").textContent = "📦 Это материал";
            overlay.querySelector("p").textContent = "Отсканированный код относится к материалу на складе.";
            overlay.querySelector("#tool-hint-link").href = "/take";
            overlay.querySelector("#tool-hint-link").textContent = "Перейти к материалам";
            overlay.style.display = "flex";
          }
          return;
        }

        if (parsed.type === "legacy") { showLegacyModal(); return; }

        const card = document.querySelector('.tool-card[data-tool-id="' + parsed.id + '"]');
        if (!card) {
          // Не на этой странице — редирект
          if (confirm("Инструмент найден, но не в текущем списке. Перейти к нему?")) {
            window.location.href = "/tools/" + parsed.id;
          }
          return;
        }
        card.scrollIntoView({ behavior: "smooth", block: "center" });
        highlight(card, 2400);
      });
      return;
    }
  };
})();

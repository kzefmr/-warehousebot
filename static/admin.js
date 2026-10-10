// admin_stock.html — модалка операций
(function () {
  const overlay = document.getElementById("modal-overlay");
  const modalForm = document.getElementById("modal-form");
  const modalTitle = document.getElementById("modal-title");
  const modalCatalogId = document.getElementById("modal-catalog-id");
  const modalItemName = document.getElementById("modal-item-name");
  const modalStockInfo = document.getElementById("modal-stock-info");
  const modalUserField = document.getElementById("modal-user-field");
  const modalUserSelect = document.getElementById("modal-user-select");
  const modalUserLabel = document.getElementById("modal-user-label");
  const modalQuantity = document.getElementById("modal-quantity");
  const modalNote = document.getElementById("modal-note");

  if (!overlay) return;

  function openModal(btn) {
    const op = btn.dataset.op;
    const cfg = window.OP_CONFIG[op];
    if (!cfg) return;

    modalTitle.textContent = cfg.title;
    modalForm.action = cfg.action;
    modalCatalogId.value = btn.dataset.catalogId;
    modalItemName.textContent = btn.dataset.name;

    if (cfg.showStock) {
      modalStockInfo.textContent = "На складе: " + btn.dataset.stock + " " + btn.dataset.unit;
      modalStockInfo.style.display = "block";
    } else {
      modalStockInfo.style.display = "none";
    }

    if (cfg.user) {
      modalUserField.style.display = "block";
      modalUserSelect.required = true;
      modalUserSelect.value = "";
      modalUserSelect.name = cfg.userFieldName || "to_user_id";
      // Обновить текст label
      modalUserLabel.childNodes[0].nodeValue = cfg.userLabel + " ";
    } else {
      modalUserField.style.display = "none";
      modalUserSelect.required = false;
    }

    modalQuantity.value = "";
    modalNote.value = "";

    overlay.style.display = "flex";
    setTimeout(function () { modalQuantity.focus(); }, 50);
  }

  function closeModal() {
    overlay.style.display = "none";
  }

  // Все кнопки действий
  document.querySelectorAll(".admin-btn").forEach(function (btn) {
    btn.addEventListener("click", function () { openModal(btn); });
  });

  // Быстрые кнопки
  document.querySelectorAll(".quick-buttons .chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      modalQuantity.value = chip.dataset.qty;
    });
  });

  // Закрытие
  document.getElementById("modal-close").addEventListener("click", closeModal);
  document.getElementById("modal-cancel").addEventListener("click", closeModal);
  overlay.addEventListener("click", function (e) {
    if (e.target === overlay) closeModal();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeModal();
  });
})();

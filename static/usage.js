// backpack_usage.html — групповой расход
(function () {
  const list = document.querySelector(".list");
  const cartCountEl = document.getElementById("cart-count");
  const cartData = document.getElementById("cart-data");
  const submitBtn = document.getElementById("submit-btn");

  if (!list) return;

  const cart = {};

  function update() {
    const ids = Object.keys(cart).filter(k => cart[k] > 0);
    cartCountEl.textContent = ids.length;
    if (ids.length > 0) {
      cartData.value = JSON.stringify(cart);
      submitBtn.disabled = false;
    } else {
      cartData.value = "";
      submitBtn.disabled = true;
    }
  }

  function setInput(id, qty, input) {
    if (qty > 0) {
      input.value = qty;
      input.classList.add("filled");
    } else {
      input.value = "";
      input.classList.remove("filled");
    }
  }

  list.querySelectorAll(".qty-plus").forEach(btn => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      const max = parseInt(btn.dataset.max || "0");
      const cur = parseInt(cart[id] || 0);
      if (cur >= max) return;
      cart[id] = cur + 1;
      const input = list.querySelector('.qty-input[data-id="' + id + '"]');
      setInput(id, cart[id], input);
      update();
      if (navigator.vibrate) navigator.vibrate(10);
    });
  });

  list.querySelectorAll(".qty-minus").forEach(btn => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.id;
      const cur = parseInt(cart[id] || 0);
      const next = Math.max(0, cur - 1);
      if (next === 0) delete cart[id]; else cart[id] = next;
      const input = list.querySelector('.qty-input[data-id="' + id + '"]');
      setInput(id, next, input);
      update();
      if (navigator.vibrate) navigator.vibrate(10);
    });
  });

  list.querySelectorAll(".qty-input").forEach(input => {
    input.addEventListener("input", () => {
      const id = input.dataset.id;
      const max = parseInt(input.dataset.max || "0");
      let v = input.value.replace(/[^0-9]/g, "");
      let n = parseInt(v || "0");
      if (n > max) n = max;
      input.value = n > 0 ? n : "";
      if (n > 0) {
        cart[id] = n;
        input.classList.add("filled");
      } else {
        delete cart[id];
        input.classList.remove("filled");
      }
      update();
    });
    input.addEventListener("focus", () => input.select());
  });

  const form = document.getElementById("usage-form");
  if (form) {
    form.addEventListener("submit", (e) => {
      const ids = Object.keys(cart).filter(k => cart[k] > 0);
      if (ids.length === 0) {
        e.preventDefault();
        alert("Укажи количество хотя бы у одной позиции");
        return;
      }
      cartData.value = JSON.stringify(cart);
    });
  }

  update();
})();

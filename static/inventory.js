// admin_inventory.html — счётчик изменений
(function () {
  const inputs = document.querySelectorAll('.inv-card-input input');
  const counter = document.getElementById('inv-count');
  const submit = document.getElementById('inv-submit');

  if (!inputs.length || !counter || !submit) return;

  function update() {
    let n = 0;
    inputs.forEach(function (inp) {
      const cur = parseInt(inp.dataset.current || '0', 10);
      const v = inp.value.trim();
      if (v === '') return;
      const nv = parseInt(v, 10);
      if (isNaN(nv)) return;
      if (nv !== cur) n++;
    });
    counter.textContent = n;
    submit.disabled = n === 0;
  }

  inputs.forEach(function (inp) {
    inp.addEventListener('input', function () {
      inp.value = inp.value.replace(/[^0-9]/g, '');
      update();
    });
  });

  update();
})();

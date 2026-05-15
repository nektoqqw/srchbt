(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    tg.setHeaderColor("#0a0612");
    tg.setBackgroundColor("#0a0612");
  }

  let state = { me: null, rollLen: 5, refLink: "" };

  function initData() {
    return (tg && tg.initData) || "";
  }

  async function api(path, opts) {
    const headers = {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": initData(),
    };
    const res = await fetch(path, { ...opts, headers });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) {
      toast("Откройте приложение из бота Telegram");
      throw new Error("unauthorized");
    }
    return data;
  }

  function toast(msg) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    setTimeout(() => el.classList.add("hidden"), 3200);
  }

  function showTab(name) {
    document.querySelectorAll(".tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === name);
    });
    document.querySelectorAll(".tab-pane").forEach((p) => {
      p.classList.toggle("active", p.id === "tab-" + name);
    });
  }

  function renderStats(me) {
    const rem =
      me.searches_remaining === null
        ? "∞"
        : `${me.searches_remaining} / ${me.searches_limit}`;
    const plus = me.is_plus
      ? me.plus_expires
        ? `до ${me.plus_expires}`
        : "активна"
      : "гость";
    const luck = me.has_luck
      ? me.luck_forever
        ? "навсегда"
        : me.luck_expires || "вкл."
      : "выкл.";
    document.getElementById("stats").innerHTML = `
      <div class="stat"><strong>PLUS</strong><span>${plus}</span></div>
      <div class="stat"><strong>Удача</strong><span>${luck}${me.luck_roll_paused ? " (пауза)" : ""}</span></div>
      <div class="stat"><strong>Крутки</strong><span>${rem}</span></div>
      <div class="stat"><strong>Рефералы</strong><span>${me.referral_count}</span></div>
    `;
    const fb = document.getElementById("filtersBlock");
    if (me.is_plus) {
      fb.classList.remove("hidden");
      document.getElementById("fPre").value = me.filters.prefix || "";
      document.getElementById("fSuf").value = me.filters.suffix || "";
      document.getElementById("fDig").value = me.filters.digits || "any";
    } else {
      fb.classList.add("hidden");
    }
    document.getElementById("btnLuckToggle").textContent = me.luck_roll_paused
      ? "▶ Включить «Удачу» в подборе"
      : "⏸ Пауза «Удачи» в подборе";
  }

  function renderTariffs(data) {
    const plusEl = document.getElementById("plusTariffs");
    const luckEl = document.getElementById("luckTariffs");
    plusEl.innerHTML = (data.plus || [])
      .map(
        (t) => `
      <div class="tariff" data-kind="plus" data-key="${t.key}">
        <span>${t.title}</span>
        <span class="price">${t.price_rub} ₽</span>
      </div>`
      )
      .join("");
    luckEl.innerHTML = (data.luck || [])
      .map(
        (t) => `
      <div class="tariff" data-kind="luck" data-key="${t.key}">
        <span>${t.title}</span>
        <span class="price">${t.price_rub} ₽</span>
      </div>`
      )
      .join("");
    plusEl.querySelectorAll(".tariff").forEach((el) => {
      el.addEventListener("click", () => buyTariff(el.dataset.kind, el.dataset.key));
    });
    luckEl.querySelectorAll(".tariff").forEach((el) => {
      el.addEventListener("click", () => buyTariff(el.dataset.kind, el.dataset.key));
    });
  }

  async function buyTariff(kind, key) {
    const r = await api("/api/checkout", {
      method: "POST",
      body: JSON.stringify({ kind, tariff_key: key }),
    });
    if (!r.ok) {
      toast(r.error === "plus_required" ? "Нужна подписка PLUS" : r.error || "Ошибка");
      return;
    }
    if (r.pay_url && tg) {
      tg.openLink(r.pay_url);
    } else {
      toast("Ссылка на оплату не получена");
    }
  }

  async function loadMe() {
    const me = await api("/api/me");
    if (!me.ok) throw new Error(me.error);
    state.me = me;
    if (me.channel && !me.channel_subscribed) {
      document.getElementById("gate").classList.remove("hidden");
      document.getElementById("main").classList.add("hidden");
      document.getElementById("gateLink").href = `https://t.me/${me.channel}`;
      document.getElementById("gateText").textContent =
        me.channel_gate_error === "bot_cannot_check"
          ? "Подпишитесь на канал. Если уже подписаны — сообщите админу (бот должен быть админом канала)."
          : `Подпишитесь на @${me.channel}, затем нажмите «Проверить».`;
      return;
    }
    document.getElementById("gate").classList.add("hidden");
    document.getElementById("main").classList.remove("hidden");
    renderStats(me);
    const tariffs = await api("/api/tariffs");
    renderTariffs(tariffs);
    const ref = await api("/api/referral");
    if (ref.ok) {
      state.refLink = ref.link;
      document.getElementById("refLink").textContent = ref.link;
      document.getElementById("refCount").textContent = `Приглашено: ${ref.count} · +${ref.bonus_hours} ч PLUS за нового`;
    }
    const docs = await api("/api/documents");
    document.getElementById("docsHtml").innerHTML = docs.html || "";
    const saved = await api("/api/saved");
    const ul = document.getElementById("savedList");
    ul.innerHTML = (saved.items || [])
      .map(
        (u) =>
          `<li><span>@${u}</span><button type="button" class="btn ghost sm" data-del="${u}">✕</button></li>`
      )
      .join("");
    ul.querySelectorAll("[data-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api(`/api/saved/${btn.dataset.del}`, { method: "DELETE" });
        loadMe();
      });
    });
  }

  async function pollRoll(jobId) {
    const prog = document.getElementById("rollProgress");
    const bar = document.getElementById("rollBar");
    const status = document.getElementById("rollStatus");
    const resultEl = document.getElementById("rollResult");
    prog.classList.remove("hidden");
    resultEl.classList.add("hidden");
    let ticks = 0;
    while (ticks < 120) {
      const st = await api(`/api/roll/${jobId}`);
      if (!st.ok) break;
      const p = Math.min(95, (st.progress || 0) * 2);
      bar.style.width = p + "%";
      status.textContent = `Проверено вариантов: ${st.progress || 0}…`;
      if (st.status === "done") {
        bar.style.width = "100%";
        prog.classList.add("hidden");
        const r = st.result;
        resultEl.classList.remove("hidden");
        if (r.found) {
          resultEl.innerHTML = `
            <p class="username">@${r.username}</p>
            <p>Редкость: <b>${r.rarity}</b></p>
            <p>Ориентир: <b>$${(r.price_usd || 0).toLocaleString()}</b></p>
            <p class="muted">${r.attempts} попыток</p>
            ${state.me.is_plus ? `<button type="button" class="btn ghost sm block" id="btnSaveRoll">💾 Сохранить</button>` : ""}
          `;
          const saveBtn = document.getElementById("btnSaveRoll");
          if (saveBtn) {
            saveBtn.onclick = async () => {
              const s = await api("/api/saved", {
                method: "POST",
                body: JSON.stringify({ username: r.username }),
              });
              toast(s.ok ? "Сохранено" : s.error || "Не удалось");
            };
          }
        } else {
          resultEl.innerHTML = `<p>${r.timed_out ? "Не успели за время лимита." : "Свободный ник не найден."} Попробуйте другую длину.</p>`;
        }
        loadMe();
        return;
      }
      if (st.status === "error") {
        prog.classList.add("hidden");
        resultEl.classList.remove("hidden");
        resultEl.innerHTML = `<p class="muted">Ошибка: ${st.error || "?"}</p>`;
        return;
      }
      await new Promise((r) => setTimeout(r, 1500));
      ticks++;
    }
    status.textContent = "Долго… проверьте позже в боте.";
  }

  document.querySelectorAll(".tab").forEach((btn) => {
    btn.addEventListener("click", () => showTab(btn.dataset.tab));
  });

  document.querySelectorAll(".chip.len").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".chip.len").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.rollLen = parseInt(btn.dataset.len, 10);
    });
  });

  document.getElementById("btnRoll").addEventListener("click", async () => {
    const r = await api("/api/roll", {
      method: "POST",
      body: JSON.stringify({ length: state.rollLen }),
    });
    if (!r.ok) {
      const msgs = {
        no_attempts: "Нет бесплатных круток",
        search_blocked: "Поиск временно закрыт",
        filters_need_plus: "Фильтры только с PLUS",
      };
      toast(msgs[r.error] || r.error || "Ошибка");
      return;
    }
    pollRoll(r.job_id);
  });

  document.getElementById("btnSaveFilters").addEventListener("click", async () => {
    await api("/api/filters", {
      method: "POST",
      body: JSON.stringify({
        prefix: document.getElementById("fPre").value,
        suffix: document.getElementById("fSuf").value,
        digits: document.getElementById("fDig").value,
      }),
    });
    toast("Фильтры сохранены");
    loadMe();
  });

  document.getElementById("btnValuate").addEventListener("click", async () => {
    const text = document.getElementById("valInput").value;
    const r = await api("/api/valuate", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    const box = document.getElementById("valResults");
    if (!r.ok) {
      box.innerHTML = `<p class="muted">${r.error || "Ошибка"}</p>`;
      return;
    }
    box.innerHTML = (r.items || [])
      .map((it) => {
        if (it.error) return `<div class="val-card">@${it.username}: ${it.error}</div>`;
        const frag =
          it.fragment_listed === true
            ? "лот на Fragment"
            : it.fragment_listed === false
              ? "нет лота"
              : "";
        return `<div class="val-card">
          <strong>@${it.username}</strong>
          <p>$${it.price_usd != null ? it.price_usd.toLocaleString() : "?"} · ${it.rarity}</p>
          <p class="muted">⭐ ${it.stars}/5 · ${frag}</p>
        </div>`;
      })
      .join("");
  });

  document.getElementById("btnPromo").addEventListener("click", async () => {
    const r = await api("/api/promo", {
      method: "POST",
      body: JSON.stringify({
        code: document.getElementById("promoCode").value,
        kind: document.getElementById("promoKind").value,
      }),
    });
    toast(r.ok ? "Промокод принят" : r.reason || "Отклонено");
    if (r.ok) loadMe();
  });

  document.getElementById("btnLuckToggle").addEventListener("click", async () => {
    const r = await api("/api/luck/toggle", { method: "POST" });
    toast(r.ok ? (r.paused ? "Удача на паузе" : "Удача в подборе") : r.error || "Ошибка");
    loadMe();
  });

  document.getElementById("btnSyncPay").addEventListener("click", async () => {
    const r = await api("/api/payments/sync", { method: "POST" });
    toast((r.messages && r.messages[0]) || "Проверено");
    loadMe();
  });

  document.getElementById("btnCopyRef").addEventListener("click", () => {
    if (state.refLink && navigator.clipboard) {
      navigator.clipboard.writeText(state.refLink);
      toast("Ссылка скопирована");
    }
  });

  document.getElementById("gateRecheck").addEventListener("click", () => loadMe());

  loadMe().catch((e) => {
    console.error(e);
    toast("Не удалось загрузить. Откройте из бота.");
  });
})();

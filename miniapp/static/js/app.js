(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    const scheme = tg.colorScheme || "dark";
    const header = scheme === "light" ? "#f2f2f7" : "#000000";
    const bg = scheme === "light" ? "#f2f2f7" : "#000000";
    tg.setHeaderColor(header);
    tg.setBackgroundColor(bg);
    if (tg.disableVerticalSwipes) tg.disableVerticalSwipes();
  }

  const LETTERS = "abcdefghijklmnopqrstuvwxyz0123456789";
  const TAB_TITLES = {
    home: ["Амням", "аккаунт и статус"],
    roll: ["Крутить", "подбор свободного @ника"],
    valuate: ["Оценка", "стоимость ников"],
    shop: ["PLUS", "подписка и удача"],
    more: ["Ещё", "рефералка и документы"],
    admin: ["Админ", "пульт управления"],
  };

  let state = {
    me: null,
    rollLen: 5,
    refLink: "",
    admin: null,
    slotTimer: null,
    pollTimer: null,
  };

  function haptic(type) {
    try {
      const h = tg && tg.HapticFeedback;
      if (!h) return;
      if (type === "success" && h.notificationOccurred) h.notificationOccurred("success");
      else if (type === "error" && h.notificationOccurred) h.notificationOccurred("error");
      else if (h.impactOccurred) h.impactOccurred(type === "heavy" ? "heavy" : "light");
    } catch (_) {}
  }

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
    if (res.status === 403) {
      toast("Нет доступа");
      throw new Error("forbidden");
    }
    return data;
  }

  function toast(msg) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 3200);
  }

  function showTab(name) {
    document.querySelectorAll(".tab-item").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === name);
    });
    document.querySelectorAll(".tab-pane").forEach((p) => {
      p.classList.toggle("active", p.id === "tab-" + name);
    });
    const t = TAB_TITLES[name] || TAB_TITLES.home;
    document.getElementById("pageTitle").textContent = t[0];
    if (name === "home" && state.me) {
      renderProfile(state.me);
    } else {
      document.getElementById("pageSubtitle").textContent = t[1];
    }
    haptic("light");
  }

  function buildSlots(len) {
    const row = document.getElementById("slotRow");
    row.innerHTML = "";
    for (let i = 0; i < len; i++) {
      const cell = document.createElement("div");
      cell.className = "slot-cell";
      const strip = document.createElement("div");
      strip.className = "slot-strip";
      for (let j = 0; j < 8; j++) {
        const ch = document.createElement("span");
        ch.className = "slot-char";
        ch.textContent = LETTERS[Math.floor(Math.random() * LETTERS.length)];
        strip.appendChild(ch);
      }
      cell.appendChild(strip);
      row.appendChild(cell);
    }
  }

  function spinSlots() {
    document.querySelectorAll(".slot-strip").forEach((strip) => {
      const chars = strip.querySelectorAll(".slot-char");
      chars.forEach((ch) => {
        ch.textContent = LETTERS[Math.floor(Math.random() * LETTERS.length)];
      });
      const offset = Math.floor(Math.random() * 7) * 48;
      strip.style.transform = `translateY(-${offset}px)`;
    });
  }

  function startRollAnimation(len) {
    const stage = document.getElementById("rollStage");
    stage.classList.remove("hidden");
    stage.classList.add("is-spinning");
    buildSlots(len);
    clearInterval(state.slotTimer);
    state.slotTimer = setInterval(spinSlots, 90);
    document.getElementById("btnRoll").disabled = true;
    document.getElementById("rollResult").classList.add("hidden");
    haptic("light");
  }

  function stopRollAnimation() {
    const stage = document.getElementById("rollStage");
    stage.classList.remove("is-spinning");
    clearInterval(state.slotTimer);
    document.getElementById("btnRoll").disabled = false;
  }

  const SLOT_H = 48;

  function revealSlotLetter(cell, targetChar, delayMs) {
    return new Promise((resolve) => {
      setTimeout(() => {
        const strip = document.createElement("div");
        strip.className = "slot-strip";
        const total = 14;
        const targetIdx = total - 2;
        for (let j = 0; j < total; j++) {
          const span = document.createElement("span");
          span.className = "slot-char";
          span.textContent =
            j === targetIdx
              ? targetChar
              : LETTERS[Math.floor(Math.random() * LETTERS.length)];
          strip.appendChild(span);
        }
        cell.innerHTML = "";
        cell.appendChild(strip);
        strip.style.transition = "none";
        strip.style.transform = "translateY(0)";
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            strip.style.transition = "transform 0.62s cubic-bezier(0.15, 0.85, 0.25, 1)";
            strip.style.transform = `translateY(-${targetIdx * SLOT_H}px)`;
          });
        });
        const done = () => {
          cell.classList.add("slot-locked");
          const final = strip.querySelectorAll(".slot-char")[targetIdx];
          if (final) final.classList.add("slot-final");
          haptic("light");
          resolve();
        };
        strip.addEventListener("transitionend", done, { once: true });
        setTimeout(done, 750);
      }, delayMs);
    });
  }

  async function revealUsernameInSlots(username) {
    clearInterval(state.slotTimer);
    const stage = document.getElementById("rollStage");
    stage.classList.remove("is-spinning");
    stage.classList.remove("hidden");
    const chars = String(username || "")
      .toLowerCase()
      .replace(/^@/, "")
      .split("");
    buildSlots(chars.length);
    document.getElementById("rollStatus").textContent = "Нашли! Выкатываем буквы…";
    const cells = document.querySelectorAll(".slot-cell");
    for (let i = 0; i < chars.length; i++) {
      await revealSlotLetter(cells[i], chars[i], i * 200);
    }
    document.getElementById("rollStatus").textContent = `@${chars.join("")}`;
    haptic("success");
  }

  function renderProfile(me) {
    const name = (me.display_name || "").trim();
    const el = document.getElementById("profileName");
    if (el) el.textContent = name || "Не задано";
    const input = document.getElementById("nameInput");
    if (input && document.activeElement !== input) input.value = name;
    if (name && TAB_TITLES.home) {
      document.getElementById("pageSubtitle").textContent = `Привет, ${name}`;
    }
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
      <div class="stat"><strong>Удача</strong><span>${luck}${me.luck_roll_paused ? " · пауза" : ""}</span></div>
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

    const adminTab = document.querySelector(".tab-admin");
    if (me.is_admin) {
      adminTab.classList.remove("hidden");
    } else {
      adminTab.classList.add("hidden");
    }
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
    haptic("light");
    const r = await api("/api/checkout", {
      method: "POST",
      body: JSON.stringify({ kind, tariff_key: key }),
    });
    if (!r.ok) {
      toast(r.error === "plus_required" ? "Нужна подписка PLUS" : r.error || "Ошибка");
      haptic("error");
      return;
    }
    if (r.pay_url && tg) {
      tg.openLink(r.pay_url);
    } else {
      toast("Ссылка на оплату не получена");
    }
  }

  function renderAdmin(d) {
    if (!d || !d.ok) return;
    state.admin = d;
    const blocked = d.search_blocked;
    document.getElementById("btnAdminSearch").textContent = blocked
      ? "🔓 Разблокировать поиск"
      : "🔒 Заблокировать поиск";
    document.getElementById("adminStats").innerHTML = `
      <div class="stat"><strong>Пользователей</strong><span>${d.users_total}</span></div>
      <div class="stat"><strong>С PLUS</strong><span>${d.users_plus}</span></div>
      <div class="stat"><strong>Поиск</strong><span>${blocked ? "закрыт" : "открыт"}</span></div>
    `;
    const sel = document.getElementById("admTariff");
    const act = document.getElementById("admAction").value;
    updateAdminTariffSelect(act, d);

    document.getElementById("admPromoList").innerHTML = (d.promos || [])
      .map(
        (p) =>
          `<li><span>${p.code}</span><span>${p.kind} · ${p.uses}/${p.max_uses || "∞"}</span></li>`
      )
      .join("");
  }

  function updateAdminTariffSelect(action, d) {
    const sel = document.getElementById("admTariff");
    const hours = document.getElementById("admHours");
    if (action === "plus_hours") {
      sel.classList.add("hidden");
      hours.classList.remove("hidden");
      return;
    }
    hours.classList.add("hidden");
    if (action === "plus_tariff") {
      sel.classList.remove("hidden");
      sel.innerHTML = (d.plus_tariffs || [])
        .map((t) => `<option value="${t.key}">${t.title}</option>`)
        .join("");
    } else if (action === "luck_tariff") {
      sel.classList.remove("hidden");
      sel.innerHTML = (d.luck_tariffs || [])
        .map((t) => `<option value="${t.key}">${t.title}</option>`)
        .join("");
    } else {
      sel.classList.add("hidden");
    }
  }

  async function loadAdmin() {
    if (!state.me || !state.me.is_admin) return;
    const d = await api("/api/admin/dashboard");
    renderAdmin(d);
  }

  async function loadMe() {
    const me = await api("/api/me");
    if (!me.ok) throw new Error(me.error);
    state.me = me;
    if (me.channel && !me.channel_subscribed) {
      document.getElementById("gate").classList.remove("hidden");
      document.getElementById("main").classList.add("hidden");
      document.getElementById("tabBar").classList.add("hidden");
      document.getElementById("gateLink").href = `https://t.me/${me.channel}`;
      document.getElementById("gateText").textContent =
        me.channel_gate_error === "bot_cannot_check"
          ? "Подпишитесь на канал. Если уже подписаны — напишите в поддержку."
          : `Подпишитесь на @${me.channel}, затем нажмите «Проверить».`;
      return;
    }
    document.getElementById("gate").classList.add("hidden");
    document.getElementById("main").classList.remove("hidden");
    document.getElementById("tabBar").classList.remove("hidden");
    renderStats(me);
    renderProfile(me);
    const tariffs = await api("/api/tariffs");
    renderTariffs(tariffs);
    const ref = await api("/api/referral");
    if (ref.ok) {
      state.refLink = ref.link;
      document.getElementById("refLink").textContent = ref.link;
      document.getElementById("refCount").textContent = `Приглашено: ${ref.count} · +${ref.bonus_hours} ч PLUS`;
    }
    const docs = await api("/api/documents");
    document.getElementById("docsHtml").innerHTML = docs.html || "";
    const saved = await api("/api/saved");
    const ul = document.getElementById("savedList");
    ul.innerHTML = (saved.items || [])
      .map(
        (u) =>
          `<li><span>@${u}</span><button type="button" class="btn btn-plain btn-sm" data-del="${u}">✕</button></li>`
      )
      .join("");
    ul.querySelectorAll("[data-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api(`/api/saved/${btn.dataset.del}`, { method: "DELETE" });
        loadMe();
      });
    });
    if (me.is_admin) loadAdmin();
  }

  function showRollResult(r) {
    const resultEl = document.getElementById("rollResult");
    resultEl.classList.remove("hidden");
    if (r.found) {
      resultEl.className = "result-card found";
      haptic("success");
      resultEl.innerHTML = `
        <p class="username">@${r.username}</p>
        <p>Редкость: <b>${r.rarity}</b></p>
        <p>Ориентир: <b>$${(r.price_usd || 0).toLocaleString()}</b></p>
        <p class="caption">${r.attempts} попыток</p>
        ${state.me.is_plus ? `<button type="button" class="btn btn-plain btn-block" id="btnSaveRoll">Сохранить @ник</button>` : ""}
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
      resultEl.className = "result-card";
      haptic("error");
      resultEl.innerHTML = `<p>${r.timed_out ? "Не успели за лимит времени." : "Свободный ник не найден."} Попробуйте другую длину.</p>`;
    }
  }

  async function pollRoll(jobId) {
    const status = document.getElementById("rollStatus");
    const attempts = document.getElementById("rollAttempts");
    let ticks = 0;
    while (ticks < 120) {
      const st = await api(`/api/roll/${jobId}`);
      if (!st.ok) break;
      status.textContent = "Ищем свободный ник…";
      attempts.textContent = `Проверено: ${st.progress || 0}`;
      if (st.status === "done") {
        const r = st.result || {};
        document.getElementById("btnRoll").disabled = false;
        if (r.found && r.username) {
          await revealUsernameInSlots(r.username);
          await new Promise((res) => setTimeout(res, 400));
        } else {
          stopRollAnimation();
          document.getElementById("rollStage").classList.add("hidden");
        }
        showRollResult(r);
        loadMe();
        return;
      }
      if (st.status === "error") {
        stopRollAnimation();
        document.getElementById("rollStage").classList.add("hidden");
        const resultEl = document.getElementById("rollResult");
        resultEl.classList.remove("hidden");
        resultEl.className = "result-card";
        resultEl.innerHTML = `<p class="caption">Ошибка: ${st.error || "?"}</p>`;
        haptic("error");
        return;
      }
      await new Promise((r) => setTimeout(r, 1200));
      ticks++;
    }
    stopRollAnimation();
    status.textContent = "Долго… проверьте позже в боте.";
  }

  document.querySelectorAll(".tab-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      showTab(btn.dataset.tab);
      if (btn.dataset.tab === "admin") loadAdmin();
    });
  });

  document.querySelectorAll(".seg-item.len").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".seg-item.len").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.rollLen = parseInt(btn.dataset.len, 10);
      haptic("light");
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
      haptic("error");
      return;
    }
    startRollAnimation(state.rollLen);
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
    haptic("success");
    loadMe();
  });

  document.getElementById("btnValuate").addEventListener("click", async () => {
    haptic("light");
    const text = document.getElementById("valInput").value;
    const r = await api("/api/valuate", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    const box = document.getElementById("valResults");
    if (!r.ok) {
      box.innerHTML = `<p class="caption">${r.error || "Ошибка"}</p>`;
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
          <p class="caption">⭐ ${it.stars}/5 · ${frag}</p>
        </div>`;
      })
      .join("");
    haptic("success");
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
    if (r.ok) {
      haptic("success");
      loadMe();
    } else haptic("error");
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
      haptic("success");
    }
  });

  document.getElementById("gateRecheck").addEventListener("click", () => loadMe());

  document.getElementById("btnEditName").addEventListener("click", () => {
    const panel = document.getElementById("nameEditPanel");
    panel.classList.toggle("hidden");
    if (!panel.classList.contains("hidden")) {
      document.getElementById("nameInput").focus();
    }
    haptic("light");
  });

  async function saveDisplayName(name) {
    const r = await api("/api/profile/name", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    if (r.ok) {
      toast(name ? "Имя сохранено" : "Имя сброшено");
      haptic("success");
      document.getElementById("nameEditPanel").classList.add("hidden");
      loadMe();
    } else {
      toast(r.message || r.error || "Не удалось");
      haptic("error");
    }
  }

  document.getElementById("btnSaveName").addEventListener("click", () => {
    saveDisplayName(document.getElementById("nameInput").value.trim());
  });

  document.getElementById("btnClearName").addEventListener("click", () => {
    saveDisplayName("");
  });

  document.getElementById("admAction").addEventListener("change", (e) => {
    updateAdminTariffSelect(e.target.value, state.admin || {});
  });

  document.getElementById("btnAdminSearch").addEventListener("click", async () => {
    const r = await api("/api/admin/toggle-search", { method: "POST" });
    toast(r.search_blocked ? "Поиск закрыт" : "Поиск открыт");
    loadAdmin();
    haptic("success");
  });

  document.getElementById("btnAdminGrant").addEventListener("click", async () => {
    const action = document.getElementById("admAction").value;
    const body = {
      target_uid: parseInt(document.getElementById("admUid").value, 10),
      action,
    };
    if (action === "plus_tariff" || action === "luck_tariff") {
      body.tariff_key = document.getElementById("admTariff").value;
    }
    if (action === "plus_hours") {
      body.hours = parseInt(document.getElementById("admHours").value, 10) || 24;
    }
    const r = await api("/api/admin/grant", { method: "POST", body: JSON.stringify(body) });
    if (r.ok) {
      toast(r.message || "Готово");
      haptic("success");
    } else {
      const err = {
        target_needs_plus: "Сначала выдайте PLUS",
        unknown_tariff: "Неизвестный тариф",
        invalid_uid: "Неверный ID",
      };
      toast(err[r.error] || r.error || "Ошибка");
      haptic("error");
    }
  });

  document.getElementById("btnAdminBc").addEventListener("click", async () => {
    const text = document.getElementById("admBcText").value.trim();
    if (!text) {
      toast("Введите текст");
      return;
    }
    if (!confirm("Запустить рассылку?")) return;
    document.getElementById("btnAdminBc").disabled = true;
    toast("Рассылка запущена…");
    const r = await api("/api/admin/broadcast", {
      method: "POST",
      body: JSON.stringify({
        mode: document.getElementById("admBcMode").value,
        text,
      }),
    });
    document.getElementById("btnAdminBc").disabled = false;
    if (r.ok) {
      toast(`Готово: ${r.sent} доставлено, ${r.failed} пропущено`);
      haptic("success");
    } else {
      toast(r.error || "Ошибка");
      haptic("error");
    }
  });

  document.getElementById("btnAdminPromo").addEventListener("click", async () => {
    const r = await api("/api/admin/promo", {
      method: "POST",
      body: JSON.stringify({
        code: document.getElementById("admPromoCode").value,
        kind: document.getElementById("admPromoKind").value,
        max_uses: parseInt(document.getElementById("admPromoMax").value, 10) || 0,
        plus_days: parseInt(document.getElementById("admPromoDays").value, 10) || 7,
      }),
    });
    if (r.ok) {
      toast(`Промокод ${r.code} создан`);
      loadAdmin();
      haptic("success");
    } else {
      toast(r.error || "Не создано");
      haptic("error");
    }
  });

  loadMe().catch((e) => {
    console.error(e);
    toast("Не удалось загрузить. Откройте из бота.");
  });
})();

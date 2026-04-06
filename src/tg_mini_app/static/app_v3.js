/* global Telegram */

const apiBase = "";
const APP_VERSION = "v10";

/**
 * Демо при пустом image_url. Commons, CC BY 2.0 (Tim Reckmann) — указать автора в проде.
 * https://commons.wikimedia.org/wiki/File:Sushi_(14930650292).jpg
 */
const DEMO_FALLBACK_IMAGE_URL =
  "https://upload.wikimedia.org/wikipedia/commons/1/14/Sushi_%2814930650292%29.jpg";

const TOPBAR_OFFSET_PX = 56;

function syncTabsStickyUnderBanner() {
  const tabs = byId("tabs");
  const banner = byId("orderBanner");
  if (!tabs) return;
  if (!banner || banner.hidden) {
    tabs.style.top = `${TOPBAR_OFFSET_PX}px`;
    return;
  }
  const h = Math.round(banner.getBoundingClientRect().height) || 48;
  tabs.style.top = `${TOPBAR_OFFSET_PX + h}px`;
}

const LS_LAST_ORDER_ID = "last_order_id";

const ORDER_STATUS_RU = {
  pending_operator: "На согласовании у оператора",
  pending_operator_change_text: "Оператор готовит правки",
  pending_customer_change_accept: "Нужен ваш ответ по правкам (см. чат Telegram)",
  pending_customer_substitution: "Нужен ответ по замене позиций в заказе",
  awaiting_payment: "Согласован — выберите оплату в чате с ботом",
  rejected_by_operator: "Заказ не принят оператором",
  rejected_by_customer: "Заказ отменён",
  cancelled_by_customer: "Вы отменили заказ до согласования",
  cancelled_by_operator: "Заказ отменён оператором",
  active: "Заказ активен",
  out_for_delivery: "Передан в доставку",
  delivered: "Заказ доставлен",
};

const LINE_STATUS_RU = {
  ok: "",
  replaced: "заменена",
  awaiting_customer: "предложена замена",
};

/** Остановка опроса: финальные и «отмена»; active/out_for_delivery продолжаем. */
const ORDER_POLL_TERMINAL = new Set([
  "rejected_by_operator",
  "rejected_by_customer",
  "cancelled_by_customer",
  "cancelled_by_operator",
  "delivered",
]);

function rub(n) {
  const v = Number(n);
  if (Number.isNaN(v)) return "0 ₽";
  return `${Math.round(v)} ₽`;
}

function byId(id) {
  return document.getElementById(id);
}

function setHint(text) {
  const hint = byId("checkoutHint");
  if (hint) hint.textContent = `[${APP_VERSION}] ${text}`;
}

let _orderPollTimer = null;

function stopOrderPolling() {
  if (_orderPollTimer) {
    clearInterval(_orderPollTimer);
    _orderPollTimer = null;
  }
}

function paidLine(order) {
  let extra = "";
  if (order.courier_cash_received) {
    extra += "Курьер отметил получение наличных. ";
  }
  if (order.payment_received_confirmed) {
    extra += "Оплата отмечена полученной. ";
  }
  if (order.status !== "active") return extra;
  if (order.payment_type === "card") return `${extra}Оплачено (карта). `;
  if (order.payment_type === "cash") {
    return `${extra}Заказ принят. Оплата наличными курьеру. `;
  }
  return extra;
}

function renderOrderLines(order) {
  const ul = byId("orderLines");
  if (!ul) return;
  const items = Array.isArray(order.items) ? order.items : [];
  if (!items.length) {
    ul.hidden = true;
    ul.innerHTML = "";
    return;
  }
  ul.hidden = false;
  ul.innerHTML = "";
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    const st = it.line_status || "ok";
    const stRu = LINE_STATUS_RU[st] || st;
    let text = `${it.name} × ${it.qty} — ${rub(it.price)}`;
    if (stRu) text += ` (${stRu})`;
    if (st === "awaiting_customer" && it.proposed_name) {
      text += ` → предложено: ${it.proposed_name} (${rub(it.proposed_price)})`;
    }
    const li = document.createElement("li");
    li.textContent = text;
    ul.appendChild(li);
  }
}

function renderOrderStatus(order) {
  const el = byId("orderStatus");
  const banner = byId("orderBanner");
  if (!el || !order) return;
  const ru = ORDER_STATUS_RU[order.status] || order.status;
  const payExtra =
    order.payment_type && order.status !== "active"
      ? ` · способ: ${order.payment_type}`
      : "";
  const route = order.delivery_route ? ` · маршрут: ${order.delivery_route}` : "";
  el.textContent = `Заказ #${order.id}: ${paidLine(order)}${ru}${payExtra}${route}`;
  if (banner) banner.hidden = false;
  renderOrderLines(order);
  const cancelBtn = byId("cancelOrderBtn");
  if (cancelBtn) cancelBtn.hidden = order.status !== "pending_operator";
  const subst = byId("substitutionActions");
  if (subst) {
    subst.hidden = order.status !== "pending_customer_substitution";
  }
  syncTabsStickyUnderBanner();
}

function _orderFetchLooksLikeMissing(msg) {
  const s = String(msg);
  return (
    /\b404\b/.test(s) ||
    s.includes("Заказ не найден") ||
    s.includes('"detail":"Заказ не найден"')
  );
}

async function refreshOrderStatusOnce(orderId) {
  try {
    const order = await apiGetOrder(orderId);
    renderOrderStatus(order);
    return order;
  } catch (e) {
    const el = byId("orderStatus");
    const banner = byId("orderBanner");
    if (_orderFetchLooksLikeMissing(e)) {
      localStorage.removeItem(LS_LAST_ORDER_ID);
      stopOrderPolling();
      if (banner) banner.hidden = true;
      if (el) el.textContent = "";
      syncTabsStickyUnderBanner();
      return null;
    }
    if (banner) banner.hidden = false;
    if (el) {
      el.textContent = `Заказ: не удалось обновить статус (${String(e)})`;
    }
    syncTabsStickyUnderBanner();
    return null;
  }
}

function startOrderPolling(orderId) {
  stopOrderPolling();
  void refreshOrderStatusOnce(orderId);
  _orderPollTimer = setInterval(async () => {
    const o = await refreshOrderStatusOnce(orderId);
    if (o && ORDER_POLL_TERMINAL.has(o.status)) stopOrderPolling();
  }, 5000);
}

async function apiGet(path, extraHeaders = {}) {
  const r = await fetch(`${apiBase}${path}`, {
    headers: { Accept: "application/json", ...extraHeaders },
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`GET ${path} -> ${r.status} ${t}`);
  }
  return await r.json();
}

/** Заказ клиента: initData в заголовке (короче URL) или customer_tg_id в query. */
async function apiGetOrder(orderId) {
  const headers = {};
  const initData = getInitData();
  if (initData) headers["X-Telegram-Init-Data"] = initData;
  const q = new URLSearchParams();
  const tg = getTgUserId() || getTgUserIdFromInput();
  if (!initData && tg) q.set("customer_tg_id", tg);
  const qs = q.toString();
  const path = `/orders/${orderId}${qs ? `?${qs}` : ""}`;
  return await apiGet(path, headers);
}

async function apiPostCancelOrder(orderId) {
  const headers = { Accept: "application/json" };
  const initData = getInitData();
  if (initData) headers["X-Telegram-Init-Data"] = initData;
  const q = new URLSearchParams();
  const tg = getTgUserId() || getTgUserIdFromInput();
  if (!initData && tg) q.set("customer_tg_id", tg);
  const qs = q.toString();
  const path = `/orders/${orderId}/cancel${qs ? `?${qs}` : ""}`;
  const r = await fetch(`${apiBase}${path}`, { method: "POST", headers });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`POST ${path} -> ${r.status} ${t}`);
  }
  return await r.json();
}

async function apiPostOrderSubpath(orderId, subpath) {
  const headers = { Accept: "application/json" };
  const initData = getInitData();
  if (initData) headers["X-Telegram-Init-Data"] = initData;
  const q = new URLSearchParams();
  const tg = getTgUserId() || getTgUserIdFromInput();
  if (!initData && tg) q.set("customer_tg_id", tg);
  const qs = q.toString();
  const path = `/orders/${orderId}/${subpath}${qs ? `?${qs}` : ""}`;
  const r = await fetch(`${apiBase}${path}`, { method: "POST", headers });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`POST ${path} -> ${r.status} ${t}`);
  }
  return await r.json();
}

function cartAuthFields() {
  const initData = getInitData();
  const tg = getTgUserId() || getTgUserIdFromInput();
  return {
    init_data: initData || null,
    customer_tg_id: initData ? null : tg ? Number(tg) : null,
  };
}

async function apiPost(path, body, extraHeaders = {}) {
  const r = await fetch(`${apiBase}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...extraHeaders,
    },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`POST ${path} -> ${r.status} ${t}`);
  }
  return await r.json();
}

function getTgUserId() {
  try {
    if (!window.Telegram || !Telegram.WebApp) return null;
    const u = Telegram.WebApp.initDataUnsafe?.user;
    if (!u || !u.id) return null;
    return String(u.id);
  } catch (_) {
    return null;
  }
}

function getInitData() {
  try {
    if (!window.Telegram || !Telegram.WebApp) return null;
    const s = String(Telegram.WebApp.initData || "").trim();
    return s || null;
  } catch (_) {
    return null;
  }
}

/** Как на сервере выставлен APP_ENV (см. base.html data-app-env). */
function serverAppEnv() {
  try {
    const v = document.documentElement.dataset.appEnv;
    return String(v || "local")
      .trim()
      .toLowerCase();
  } catch (_) {
    return "local";
  }
}

function getTgUserIdFromInput() {
  const el = byId("tgId");
  if (!el) return null;
  const raw = String(el.value || "").trim();
  if (!raw) return null;
  if (!/^\d+$/.test(raw)) return null;
  if (raw === "0") return null;
  return raw;
}

async function ensureCart() {
  let cartId = localStorage.getItem("cart_id");
  if (cartId) {
    try {
      const existing = await apiGet(`/cart/${cartId}`);
      if (existing.status !== "open") {
        localStorage.removeItem("cart_id");
        cartId = null;
      }
    } catch {
      localStorage.removeItem("cart_id");
      cartId = null;
    }
  }
  if (!cartId) {
    const cart = await apiPost("/cart", { owner_tg_id: getTgUserId() });
    cartId = cart.id;
    localStorage.setItem("cart_id", cartId);
  }
  return cartId;
}

function setThemeFromTelegram() {
  try {
    if (!window.Telegram || !Telegram.WebApp) return;
    Telegram.WebApp.ready();
    Telegram.WebApp.expand();

    const tp = Telegram.WebApp.themeParams || {};
    const root = document.documentElement;
    for (const [k, v] of Object.entries(tp)) {
      if (!v) continue;
      const cssKey = `--tg-theme-${String(k).replaceAll("_", "-")}`;
      root.style.setProperty(cssKey, String(v));
    }

    try {
      if (tp.bg_color && typeof Telegram.WebApp.setBackgroundColor === "function") {
        Telegram.WebApp.setBackgroundColor(tp.bg_color);
      }
      if (tp.header_bg_color && typeof Telegram.WebApp.setHeaderColor === "function") {
        Telegram.WebApp.setHeaderColor(tp.header_bg_color);
      }
    } catch (_) {
      // no-op
    }
  } catch (_) {
    // no-op
  }
}

function renderTabs(categories, activeCategoryId) {
  const tabs = byId("tabs");
  tabs.innerHTML = "";

  const heading = byId("catalogHeading");
  if (heading) {
    const active = categories.find((c) => c.id === activeCategoryId);
    heading.textContent = active ? active.name : "Меню";
  }

  const makeTab = (label, id) => {
    const b = document.createElement("button");
    b.className = `tab${id === activeCategoryId ? " is-active" : ""}`;
    b.type = "button";
    b.textContent = label;
    b.addEventListener("click", () => {
      localStorage.setItem("active_category_id", String(id));
      window.location.reload();
    });
    return b;
  };

  for (const c of categories) tabs.appendChild(makeTab(c.name, c.id));
}

function hapticLight() {
  try {
    const h = window.Telegram?.WebApp?.HapticFeedback;
    if (h && typeof h.impactOccurred === "function") h.impactOccurred("light");
  } catch (_) {
    // no-op
  }
}

function renderGrid(products, onAdd) {
  const grid = byId("grid");
  grid.innerHTML = "";

  for (const p of products) {
    const card = document.createElement("article");
    card.className = "card";

    const galleryRaw = Array.isArray(p.image_gallery) ? p.image_gallery : [];
    const gallery = galleryRaw
      .map((u) => String(u || "").trim())
      .filter(Boolean);
    const fallbackMain =
      (p.image_url && String(p.image_url).trim()) || gallery[0] || "";
    const urls = gallery.length ? gallery : fallbackMain ? [fallbackMain] : [];

    const imgWrap = document.createElement("div");
    imgWrap.className = "card__img";

    let imgIndex = 0;
    const paintImg = () => {
      imgWrap.innerHTML = "";
      const src = urls.length ? urls[imgIndex] : DEMO_FALLBACK_IMAGE_URL;
      if (!urls.length) {
        imgWrap.textContent = "Фото";
        imgWrap.classList.add("is-placeholder");
        return;
      }
      imgWrap.classList.remove("is-placeholder");
      const img = document.createElement("img");
      img.alt = p.name;
      img.loading = "lazy";
      img.decoding = "async";
      img.referrerPolicy = "no-referrer";
      img.src = src;
      img.addEventListener("error", () => {
        imgWrap.innerHTML = "";
        imgWrap.textContent = "Фото";
        imgWrap.classList.add("is-placeholder");
      });
      imgWrap.appendChild(img);
      if (urls.length > 1) {
        const nav = document.createElement("div");
        nav.className = "card__img-nav";
        const prev = document.createElement("button");
        prev.type = "button";
        prev.className = "card__img-btn";
        prev.setAttribute("aria-label", "Предыдущее фото");
        prev.textContent = "‹";
        prev.addEventListener("click", (ev) => {
          ev.stopPropagation();
          imgIndex = (imgIndex - 1 + urls.length) % urls.length;
          paintImg();
        });
        const next = document.createElement("button");
        next.type = "button";
        next.className = "card__img-btn";
        next.setAttribute("aria-label", "Следующее фото");
        next.textContent = "›";
        next.addEventListener("click", (ev) => {
          ev.stopPropagation();
          imgIndex = (imgIndex + 1) % urls.length;
          paintImg();
        });
        nav.appendChild(prev);
        nav.appendChild(next);
        imgWrap.appendChild(nav);
      }
    };
    paintImg();

    const body = document.createElement("div");
    body.className = "card__body";

    const titleRow = document.createElement("div");
    titleRow.className = "card__title-row";

    const name = document.createElement("h3");
    name.className = "card__name";
    name.textContent = p.name;

    const weight = document.createElement("span");
    weight.className = "card__weight";
    weight.textContent = p.weight_g ? `${p.weight_g} г` : "—";

    titleRow.appendChild(name);
    titleRow.appendChild(weight);

    const desc = document.createElement("p");
    desc.className = "card__desc";
    desc.textContent = p.description || "Состав и подача — уточняйте у оператора при согласовании.";

    body.appendChild(titleRow);
    body.appendChild(desc);

    const attrs = Array.isArray(p.attributes) ? p.attributes : [];
    if (attrs.length) {
      const ul = document.createElement("ul");
      ul.className = "card__attrs";
      for (const a of attrs) {
        const li = document.createElement("li");
        const an = a && a.name != null ? String(a.name) : "";
        const av = a && a.value != null ? String(a.value) : "";
        li.textContent = av ? `${an}: ${av}` : an;
        ul.appendChild(li);
      }
      body.appendChild(ul);
    }

    if (p.stock_quantity != null && Number(p.stock_quantity) >= 0) {
      const st = document.createElement("div");
      st.className = "card__stock";
      st.textContent =
        Number(p.stock_quantity) === 0
          ? "Нет в наличии"
          : `Остаток: ${p.stock_quantity} шт.`;
      body.appendChild(st);
    }

    const footer = document.createElement("div");
    footer.className = "card__footer";

    const price = document.createElement("div");
    price.className = "card__price";
    price.textContent = rub(p.price);

    const addBtn = document.createElement("button");
    addBtn.className = "product-card__add";
    addBtn.type = "button";
    if (!p.is_available) {
      addBtn.classList.add("is-unavailable");
      addBtn.textContent = "Нет в наличии";
      addBtn.disabled = true;
    } else {
      addBtn.textContent = "В корзину";
      addBtn.addEventListener("click", () => {
        hapticLight();
        void onAdd(p.id);
      });
    }

    footer.appendChild(price);
    footer.appendChild(addBtn);

    body.appendChild(footer);

    card.appendChild(imgWrap);
    card.appendChild(body);
    grid.appendChild(card);
  }
}

function drawerOpen() {
  byId("drawer").hidden = false;
}
function drawerClose() {
  byId("drawer").hidden = true;
}

function renderCart(cart, onDelta) {
  const el = byId("cartItems");
  el.innerHTML = "";

  if (!cart.items.length) {
    const empty = document.createElement("div");
    empty.className = "hint";
    empty.textContent = "Корзина пуста — добавьте позиции из каталога.";
    el.appendChild(empty);
    return;
  }

  for (const it of cart.items) {
    const row = document.createElement("div");
    row.className = "cart-row";

    const left = document.createElement("div");
    const name = document.createElement("div");
    name.className = "cart-row__name";
    name.textContent = it.name;
    const meta = document.createElement("div");
    meta.className = "cart-row__meta";
    meta.textContent = `${rub(it.price)} • ${rub(it.subtotal)}`;
    left.appendChild(name);
    left.appendChild(meta);

    const qty = document.createElement("div");
    qty.className = "qty";
    const minus = document.createElement("button");
    minus.type = "button";
    minus.textContent = "–";
    minus.addEventListener("click", () => onDelta(it.product_id, -1));
    const val = document.createElement("div");
    val.className = "qty__value";
    val.textContent = String(it.qty);
    const plus = document.createElement("button");
    plus.type = "button";
    plus.textContent = "+";
    plus.addEventListener("click", () => onDelta(it.product_id, 1));
    qty.appendChild(minus);
    qty.appendChild(val);
    qty.appendChild(plus);

    row.appendChild(left);
    row.appendChild(qty);
    el.appendChild(row);
  }
}

async function main() {
  setThemeFromTelegram();
  setHint("Приложение загружено.");

  const checkoutBtn = byId("checkoutBtn");
  if (!checkoutBtn) return;

  const [categories, products] = await Promise.all([
    apiGet("/catalog/categories"),
    apiGet("/catalog/products"),
  ]);

  let activeCategoryId = Number(localStorage.getItem("active_category_id") || "");
  if (!activeCategoryId || !categories.some((c) => c.id === activeCategoryId)) {
    activeCategoryId = categories[0]?.id ?? 0;
    if (activeCategoryId) localStorage.setItem("active_category_id", String(activeCategoryId));
  }

  renderTabs(categories, activeCategoryId);
  syncTabsStickyUnderBanner();
  window.addEventListener("resize", () => syncTabsStickyUnderBanner());

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    const oid = localStorage.getItem(LS_LAST_ORDER_ID);
    if (oid && /^\d+$/.test(oid)) void refreshOrderStatusOnce(Number(oid));
  });

  {
    const signed = Boolean(getInitData());
    const local = serverAppEnv() === "local";
    const f = byId("tgIdField");
    if (f) {
      if (signed) f.hidden = true;
      else if (local) f.hidden = false;
      else f.hidden = true;
    }
    if (!signed && local) {
      setHint(
        "APP_ENV=local: при отладке в браузере укажите свой Telegram ID внизу корзины.",
      );
    } else if (!signed && !local) {
      setHint(
        "Откройте меню из Telegram (кнопка в боте). В обычном браузере без initData заказ " +
          "не пройдёт, пока на сервере не задан APP_ENV=local.",
      );
    }
  }

  let cartId = await ensureCart();

  async function refreshCart() {
    const cart = await apiGet(`/cart/${cartId}`);
    byId("cartSum").textContent = rub(cart.total);
    renderCart(cart, async (productId, delta) => {
      const updated = await apiPost(`/cart/${cartId}/items`, {
        product_id: productId,
        qty_delta: delta,
        ...cartAuthFields(),
      });
      byId("cartSum").textContent = rub(updated.total);
      renderCart(updated, arguments.callee);
    });
    return cart;
  }

  renderGrid(
    products.filter((p) => p.category_id === activeCategoryId),
    async (productId) => {
      await apiPost(`/cart/${cartId}/items`, {
        product_id: productId,
        qty_delta: 1,
        ...cartAuthFields(),
      });
      await refreshCart();
    },
  );

  byId("cartFab").addEventListener("click", async () => {
    drawerOpen();
    await refreshCart();
  });
  byId("drawerClose").addEventListener("click", drawerClose);
  byId("drawerScrim").addEventListener("click", drawerClose);

  const savedOrderId = localStorage.getItem(LS_LAST_ORDER_ID);
  if (savedOrderId && /^\d+$/.test(savedOrderId)) {
    startOrderPolling(Number(savedOrderId));
  }

  const cancelOrderBtn = byId("cancelOrderBtn");
  if (cancelOrderBtn) {
    cancelOrderBtn.addEventListener("click", async () => {
      const oid = localStorage.getItem(LS_LAST_ORDER_ID);
      if (!oid || !/^\d+$/.test(oid)) return;
      cancelOrderBtn.disabled = true;
      try {
        await apiPostCancelOrder(Number(oid));
        await refreshOrderStatusOnce(Number(oid));
        stopOrderPolling();
        setHint("Заказ отменён. Корзина снова доступна — можно оформить новый заказ.");
      } catch (e) {
        setHint(`Не удалось отменить заказ: ${String(e)}`);
      } finally {
        cancelOrderBtn.disabled = false;
      }
    });
  }

  const acceptSubstBtn = byId("acceptSubstBtn");
  const rejectSubstBtn = byId("rejectSubstBtn");
  if (acceptSubstBtn) {
    acceptSubstBtn.addEventListener("click", async () => {
      const oid = localStorage.getItem(LS_LAST_ORDER_ID);
      if (!oid || !/^\d+$/.test(oid)) return;
      acceptSubstBtn.disabled = true;
      try {
        await apiPostOrderSubpath(Number(oid), "substitutions/accept");
        await refreshOrderStatusOnce(Number(oid));
        setHint("Замены приняты. Ожидайте согласования заказа оператором.");
      } catch (e) {
        setHint(`Не удалось подтвердить замены: ${String(e)}`);
      } finally {
        acceptSubstBtn.disabled = false;
      }
    });
  }
  if (rejectSubstBtn) {
    rejectSubstBtn.addEventListener("click", async () => {
      const oid = localStorage.getItem(LS_LAST_ORDER_ID);
      if (!oid || !/^\d+$/.test(oid)) return;
      rejectSubstBtn.disabled = true;
      try {
        await apiPostOrderSubpath(Number(oid), "substitutions/reject");
        await refreshOrderStatusOnce(Number(oid));
        stopOrderPolling();
        setHint("Замены отклонены, заказ отменён. Можно собрать корзину заново.");
      } catch (e) {
        setHint(`Не удалось отклонить замены: ${String(e)}`);
      } finally {
        rejectSubstBtn.disabled = false;
      }
    });
  }

  checkoutBtn.addEventListener("click", async () => {
    checkoutBtn.disabled = true;
    checkoutBtn.textContent = "Отправляю…";
    try {
      const cart = await refreshCart();
      const address = byId("address").value.trim();
      const deliveryTime = byId("deliveryTime").value.trim();

      if (!cart.items.length) {
        setHint("Добавьте позиции в корзину.");
        return;
      }
      if (!address || !deliveryTime) {
        setHint("Заполните адрес и время доставки.");
        return;
      }

      const initData = getInitData();
      const customerTgId = getTgUserId() || getTgUserIdFromInput();
      if (!initData && serverAppEnv() !== "local") {
        setHint(
          "Без initData заказ не принимается (на сервере не local). Откройте витрину кнопкой Web App в боте.",
        );
        return;
      }
      if (!initData && !customerTgId) {
        const f = byId("tgIdField");
        if (f) f.hidden = false;
        const input = byId("tgId");
        if (input) input.focus();
        setHint("Нет initData. Введите Telegram ID (режим local) и нажмите ещё раз.");
        return;
      }

      setHint(`Отправляю заказ… (initData=${initData ? "ok" : "нет"}, tg_id=${customerTgId || "нет"})`);
      const orderHeaders = {};
      if (initData) orderHeaders["X-Telegram-Init-Data"] = initData;
      const order = await apiPost(
        "/orders",
        {
          cart_id: cartId,
          init_data: initData,
          customer_tg_id: customerTgId ? Number(customerTgId) : null,
          address,
          delivery_time: deliveryTime,
          customer_comment: "",
        },
        orderHeaders,
      );
      localStorage.removeItem("cart_id");
      localStorage.setItem(LS_LAST_ORDER_ID, String(order.id));
      cartId = await ensureCart();
      await refreshCart();
      setHint(`Заказ #${order.id} отправлен на согласование. Ожидайте ответ в чате Telegram.`);
      startOrderPolling(order.id);
    } catch (e) {
      setHint(`Ошибка при отправке: ${String(e)}`);
    } finally {
      checkoutBtn.disabled = false;
      checkoutBtn.textContent = "Согласовать заказ";
    }
  });
}

main().catch((e) => {
  setHint(`Ошибка загрузки: ${String(e)}`);
});


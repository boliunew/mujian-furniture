// Talks to the Flask backend. The preview build swaps this file for a
// browser-only mock with the same methods (tools/preview/mock-api.js).
(function () {
  async function j(url, opt = {}) {
    const init = { credentials: "same-origin", ...opt };
    if (opt.json !== undefined) {
      init.method = init.method || "POST";
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(opt.json);
    }
    const res = await fetch(url, init);
    let data = {};
    try { data = await res.json(); } catch (e) {}
    if (!res.ok) throw Object.assign(new Error(data.error || `Request failed (${res.status})`), { status: res.status });
    return data;
  }
  const form = (field, blobs) => {
    const fd = new FormData();
    [].concat(blobs).forEach((b, i) => fd.append(field, b, `${String(i).padStart(2, "0")}.jpg`));
    return fd;
  };

  window.api = {
    mode: "live",
    store: () => j("/api/store"),
    products: () => j("/api/products"),
    checkout: (items) => j("/api/checkout", { json: { items, lang: window.LANG } }),
    order: (id) => j("/api/order/" + encodeURIComponent(id)),
    schedule: (id, tb) => j(`/api/schedule/${encodeURIComponent(id)}?t=${encodeURIComponent(tb)}`),
    bookDelivery: (id, body) => j("/api/schedule/" + encodeURIComponent(id), { json: body }),

    me: () => j("/api/admin/me"),
    login: (password) => j("/api/admin/login", { json: { password } }),
    logout: () => j("/api/admin/logout", { method: "POST" }),
    orders: (status, q) => j("/api/admin/orders?" + new URLSearchParams(Object.entries({ status, q }).filter(([, v]) => v))),
    exportUrl: (status, q) => "/api/admin/orders.csv?" + new URLSearchParams(Object.entries({ status, q }).filter(([, v]) => v)),
    getOrder: (id) => j("/api/admin/orders/" + encodeURIComponent(id)),
    updateOrder: (id, patch) => j("/api/admin/orders/" + encodeURIComponent(id), { json: patch }),
    testOrder: () => j("/api/admin/test-order", { method: "POST" }),
    printQueue: () => j("/api/admin/print-queue"),
    ackPrint: (ids, by) => j("/api/admin/print-queue/ack", { json: { ids, by } }),
    addNote: (id, b) => j(`/api/admin/orders/${encodeURIComponent(id)}/notes`, { json: b }),
    updateNote: (id, nid, b) => j(`/api/admin/orders/${encodeURIComponent(id)}/notes/${nid}`, { json: b }),
    deleteNote: (id, nid) => j(`/api/admin/orders/${encodeURIComponent(id)}/notes/${nid}`, { method: "DELETE" }),
    todos: (status) => j("/api/admin/todos?status=" + (status || "open")),
    todoSummary: () => j("/api/admin/todos/summary"),
    saveTodo: (b) => j("/api/admin/todos", { json: b }),
    deleteTodo: (id) => j("/api/admin/todos/" + encodeURIComponent(id), { method: "DELETE" }),
    mailSettings: () => j("/api/admin/mail"),
    saveMail: (b) => j("/api/admin/mail", { json: b }),
    testMail: (to) => j("/api/admin/mail/test", { json: { to } }),
    emails: (status) => j("/api/admin/emails" + (status ? "?status=" + status : "")),
    resendEmail: (id) => j(`/api/admin/emails/${id}/resend`, { method: "POST" }),
    resendBooking: (id) => j(`/api/admin/orders/${encodeURIComponent(id)}/resend-booking`, { method: "POST" }),
    deliveries: (from, days) => j(`/api/admin/deliveries?from=${from || ""}&days=${days || 14}`),
    settings: () => j("/api/admin/settings"),
    saveSettings: (s) => j("/api/admin/settings", { json: s }),
    sendReminders: () => j("/api/admin/send-reminders", { method: "POST" }),

    inventory: () => j("/api/admin/inventory"),
    adjustStock: (body) => j("/api/admin/inventory/adjust", { json: body }),
    receive: (body) => j("/api/admin/inventory/receive", { json: body }),
    transfer: (body) => j("/api/admin/inventory/transfer", { json: body }),
    movements: (f = {}) => j("/api/admin/movements?" + new URLSearchParams(Object.entries(f).filter(([, v]) => v))),
    saveWarehouse: (w) => j("/api/admin/warehouses", { json: w }),
    deleteWarehouse: (id) => j("/api/admin/warehouses/" + encodeURIComponent(id), { method: "DELETE" }),
    counts: () => j("/api/admin/counts"),
    newCount: (body) => j("/api/admin/counts", { json: body }),
    getCount: (id) => j("/api/admin/counts/" + encodeURIComponent(id)),
    updateCount: (id, body) => j("/api/admin/counts/" + encodeURIComponent(id), { json: body }),

    adminProducts: () => j("/api/admin/products"),
    saveProduct: (p) => j("/api/admin/products", { json: p }),
    deleteProduct: (id) => j("/api/admin/products/" + id, { method: "DELETE" }),
    uploadPhoto: (id, blob) => j(`/api/admin/products/${id}/photo`, { method: "POST", body: form("photo", blob) }),
    uploadSpin: (id, blobs) => j(`/api/admin/products/${id}/spin`, { method: "POST", body: form("frames", blobs) }),
    clearSpin: (id) => j(`/api/admin/products/${id}/spin`, { method: "DELETE" }),
  };
})();

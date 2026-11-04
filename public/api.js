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
    checkout: (items) => j("/api/checkout", { json: { items } }),
    order: (id) => j("/api/order/" + encodeURIComponent(id)),

    me: () => j("/api/admin/me"),
    login: (password) => j("/api/admin/login", { json: { password } }),
    logout: () => j("/api/admin/logout", { method: "POST" }),
    orders: (status) => j("/api/admin/orders" + (status ? "?status=" + status : "")),
    getOrder: (id) => j("/api/admin/orders/" + encodeURIComponent(id)),
    updateOrder: (id, patch) => j("/api/admin/orders/" + encodeURIComponent(id), { json: patch }),
    testOrder: () => j("/api/admin/test-order", { method: "POST" }),

    adminProducts: () => j("/api/admin/products"),
    saveProduct: (p) => j("/api/admin/products", { json: p }),
    deleteProduct: (id) => j("/api/admin/products/" + id, { method: "DELETE" }),
    uploadPhoto: (id, blob) => j(`/api/admin/products/${id}/photo`, { method: "POST", body: form("photo", blob) }),
    uploadSpin: (id, blobs) => j(`/api/admin/products/${id}/spin`, { method: "POST", body: form("frames", blobs) }),
    clearSpin: (id) => j(`/api/admin/products/${id}/spin`, { method: "DELETE" }),
  };
})();

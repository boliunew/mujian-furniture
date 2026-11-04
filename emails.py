"""Customer emails in the language the customer shopped in (en / zh / es)."""
from datetime import date

LANGS = ("en", "zh", "es")

T = {
    "en": {
        "hi": "Hi {name},", "there": "there",
        "book_subject": "Book your delivery for order {no}",
        "book_body": "Thanks for your order {no}. Your furniture ships by truck, so please pick a delivery day.",
        "book_link": "Book your delivery window here: {url}",
        "adult": "Someone 18 or older needs to be home to sign for the delivery.",
        "booked_subject": "Delivery booked for order {no}", "changed_subject": "Delivery update for order {no}",
        "reminder_subject": "Your delivery is tomorrow ({no})",
        "booked": "Your delivery is booked:", "changed": "Your delivery time has changed:",
        "reminder": "Reminder: your furniture arrives tomorrow:",
        "path": "Please keep the path from the truck to the room clear, and make sure someone 18+ is home.",
        "change": "Need a different time? {url}",
        "ship_subject": "Your order {no} is on its way", "shipped": "Good news: your Mujian order {no} is on its way.",
        "tracking": "Carrier: {carrier}   Tracking number: {tracking}", "track": "Track it here: {url}",
        "items": "Items:", "thanks": "Thank you for shopping with us.",
        "ltl_note": "The carrier will call 24–48 hours before delivery to confirm a 4-hour window.",
    },
    "zh": {
        "hi": "{name}，你好：", "there": "",
        "book_subject": "请为订单 {no} 预约送货时间",
        "book_body": "谢谢你的订单 {no}。你的家具用卡车送，请选一个送货日期。",
        "book_link": "在这里预约送货时段：{url}",
        "adult": "送货时需要有 18 岁以上的人在家签收。",
        "booked_subject": "订单 {no} 已预约送货", "changed_subject": "订单 {no} 的送货时间有更新",
        "reminder_subject": "你的家具明天送到（{no}）",
        "booked": "你的送货时间已预约：", "changed": "你的送货时间已更改：",
        "reminder": "提醒：你的家具明天送到：",
        "path": "请保持从卡车到房间的通道畅通，并确保有 18 岁以上的人在家。",
        "change": "需要换时间？{url}",
        "ship_subject": "你的订单 {no} 已发货", "shipped": "好消息：你的木间订单 {no} 已经出发了。",
        "tracking": "承运商：{carrier}　运单号：{tracking}", "track": "查看物流：{url}",
        "items": "商品：", "thanks": "谢谢你的购买。",
        "ltl_note": "承运商会在送货前 24–48 小时打电话确认一个 4 小时的时间段。",
    },
    "es": {
        "hi": "Hola, {name}:", "there": "",
        "book_subject": "Reserva la entrega de tu pedido {no}",
        "book_body": "Gracias por tu pedido {no}. Tus muebles llegan en camión, así que elige un día de entrega.",
        "book_link": "Reserva tu franja de entrega aquí: {url}",
        "adult": "Debe haber alguien mayor de 18 años en casa para firmar la entrega.",
        "booked_subject": "Entrega reservada para el pedido {no}", "changed_subject": "Cambio en la entrega del pedido {no}",
        "reminder_subject": "Tu entrega es mañana ({no})",
        "booked": "Tu entrega está reservada:", "changed": "La hora de tu entrega cambió:",
        "reminder": "Recordatorio: tus muebles llegan mañana:",
        "path": "Deja despejado el camino del camión a la habitación y asegúrate de que haya alguien mayor de 18 años en casa.",
        "change": "¿Necesitas otra hora? {url}",
        "ship_subject": "Tu pedido {no} va en camino", "shipped": "Buenas noticias: tu pedido de Mujian {no} va en camino.",
        "tracking": "Transportista: {carrier}   Número de seguimiento: {tracking}", "track": "Rastréalo aquí: {url}",
        "items": "Productos:", "thanks": "Gracias por tu compra.",
        "ltl_note": "El transportista llamará 24–48 horas antes para confirmar una franja de 4 horas.",
    },
}
WEEKDAYS = {"zh": "一二三四五六日", "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]}
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def lang_of(o):
    return o.get("lang") if o.get("lang") in LANGS else "en"


def tx(lang, key, **kw):
    return T[lang][key].format(**kw)


def hi(lang, o):
    name = (o.get("ship_name") or "").strip()
    if name:
        return tx(lang, "hi", name=name)
    return {"en": "Hi there,", "zh": "你好：", "es": "Hola:"}[lang]


def when(lang, iso, window):
    d = date.fromisoformat(iso)
    if lang == "zh":
        return f"{d.month}月{d.day}日（周{WEEKDAYS['zh'][d.weekday()]}）{window}"
    if lang == "es":
        return f"{WEEKDAYS['es'][d.weekday()]} {d.day} de {MONTHS_ES[d.month - 1]}, {window}"
    return f"{d.strftime('%A, %B')} {d.day}, {window}"


def booking(o, url):
    L = lang_of(o)
    body = [hi(L, o), "", tx(L, "book_body", no=o["order_no"]), tx(L, "book_link", url=url), "", tx(L, "adult")]
    return tx(L, "book_subject", no=o["order_no"]), body


def appointment(o, window, url, kind):
    L = lang_of(o)
    body = [hi(L, o), "", T[L][kind], f"  {when(L, o['appt_date'], window)}", f"  {o['ship_line1']}, {o['ship_city']}"]
    if o.get("delivery_type") == "ltl":
        body += ["", T[L]["ltl_note"]]
    body += ["", T[L]["path"], tx(L, "change", url=url)]
    return tx(L, f"{kind}_subject", no=o["order_no"]), body


def shipped(o, item_names):
    L = lang_of(o)
    body = [hi(L, o), "", tx(L, "shipped", no=o["order_no"])]
    if o.get("carrier") and o.get("tracking"):
        body.append(tx(L, "tracking", carrier=o["carrier"], tracking=o["tracking"]))
        if o.get("tracking_url"):
            body.append(tx(L, "track", url=o["tracking_url"]))
    body += ["", T[L]["items"]] + [f"  • {n} × {q}" for n, q in item_names]
    body += ["", T[L]["thanks"]]
    return tx(L, "ship_subject", no=o["order_no"]), body

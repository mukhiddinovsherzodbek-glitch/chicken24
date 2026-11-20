"""Mini App: bot natijasini (?ok= / ?rej= / ?cx=) qo'llash - miniapp/result.js (node orqali).

Natija har ochilishda qayta keladi (bot havolaga bazadagi oxirgi natijani qo'yadi), shuning uchun
qo'llash idempotent va indamasdan (toast/banner yo'q) bo'lishi kerak.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
ROOT = BOT_DIR.parent
MINIAPP = ROOT / "miniapp"
sys.path.insert(0, str(BOT_DIR / "tests"))

from test_i18n import load_miniapp_i18n, strip_js_comments  # noqa: E402

NODE = shutil.which("node")

# result.js ni node'da yuklab, ssenariylarni bajaradigan kichik dastur (JSON natija qaytaradi)
HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const ctx = { URLSearchParams, console };
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
const R = ctx.CHICKEN24_RESULT;

const base = () => [
  { localId: 'p1', status: 'pending', orderNo: '', items: [{ id: 'a', qty: 1 }] },
  { localId: 'o1', status: 'ok', orderNo: '3', items: [] },
  { localId: 'r1', status: 'rejected', reasonCode: 'limit', items: [] },
];
// Mini App ochilishini taqlid qiladi: natija qo'llanadi, savat "accepted" bo'lsa tozalanadi
function open(state, search) {
  const res = R.applyResult(state.orders, R.parseResult(search));
  if (!res) return { changed: false };
  state.orders = res.orders;
  if (res.accepted) { state.cart = []; state.clears += 1; }
  return { changed: true };
}
const out = {};
function scenario(name, search, times) {
  const st = { orders: base(), cart: ['a'], clears: 0 };
  const changes = [];
  for (let i = 0; i < times; i++) changes.push(open(st, search).changed);
  out[name] = { orders: st.orders, cart: st.cart, clears: st.clears, changes };
}
scenario('ok_twice', '?lang=ru&ok=p1&n=7', 3);
scenario('rej_twice', '?lang=uz&rej=p1&r=area', 2);
scenario('cx_pending', '?lang=uz&cx=p1', 2);
scenario('cx_ok', '?cx=o1', 2);
scenario('unknown_ref', '?ok=zzz&n=1', 1);
scenario('ok_on_rejected', '?ok=r1&n=9', 1);
scenario('ok_on_ok', '?ok=o1&n=99', 1);
scenario('lang_only', '?lang=ru', 1);
scenario('bad_values', '?rej=p1&r=<script>', 1);
scenario('bad_n', '?ok=p1&n=12a', 1);
// avval ok, keyin (bekor qilingach) cx - ikki ochilish
{
  const st = { orders: base(), cart: ['a'], clears: 0 };
  open(st, '?ok=p1&n=7'); open(st, '?ok=p1&n=7'); open(st, '?cx=p1'); open(st, '?cx=p1');
  out['ok_then_cx'] = { orders: st.orders, cart: st.cart, clears: st.clears };
}
// kirish ro'yxati o'zgartirilmaydi
{
  const orders = base();
  const copy = JSON.stringify(orders);
  R.applyResult(orders, R.parseResult('?ok=p1&n=1'));
  out['input_untouched'] = JSON.stringify(orders) === copy;
}
// --- serverdan so'ralgan holat (GET /api/order-status) ---
out['status_map'] = {
  accepted: R.statusToResult('p1', { status: 'accepted', n: 7, reason: null }),
  accepted_bad_n: R.statusToResult('p1', { status: 'accepted', n: '7; drop' }),
  rejected: R.statusToResult('p1', { status: 'rejected', n: null, reason: 'area' }),
  rejected_bad: R.statusToResult('p1', { status: 'rejected', reason: '<b>' }),
  cancelled: R.statusToResult('p1', { status: 'cancelled', n: 7 }),
  unknown: R.statusToResult('p1', { status: 'unknown' }),
  pending: R.statusToResult('p1', { status: 'pending' }),
  garbage: R.statusToResult('p1', 'oops'),
  no_ref: R.statusToResult('', { status: 'accepted', n: 1 }),
};
out['delays'] = [0, 3999, 119999, 120000, 600000, 899999, 900000, 2e6, -5, NaN].map((t) => R.pollDelay(t));
// polling natijasi ikki marta (va keyin havoladagi ?ok=) qo'llansa - holat buzilmaydi, savat bir marta tozalanadi
{
  const st = { orders: base(), cart: ['a'], clears: 0 };
  const changes = [];
  function poll(data) {
    const res = R.applyResult(st.orders, R.statusToResult('p1', data));
    if (!res) { changes.push(false); return; }
    st.orders = res.orders;
    if (res.accepted) { st.cart = []; st.clears += 1; }
    changes.push(true);
  }
  poll({ status: 'unknown' });               // bot hali olmagan
  poll({ status: 'accepted', n: 7 });
  poll({ status: 'accepted', n: 7 });        // ikkinchi marta
  changes.push(open(st, '?ok=p1&n=7').changed); // keyin havola orqali ham keldi
  poll({ status: 'cancelled', n: 7 });       // ok dan keyin bekor - kutilmagan buyurtma emas, savatga tegilmaydi
  out['poll_twice'] = { orders: st.orders, cart: st.cart, clears: st.clears, changes };
}
{
  const st = { orders: base(), cart: ['a'], clears: 0 };
  const a = R.applyResult(st.orders, R.statusToResult('p1', { status: 'rejected', reason: 'limit' }));
  st.orders = a.orders;
  const b = R.applyResult(st.orders, R.statusToResult('p1', { status: 'rejected', reason: 'limit' }));
  out['poll_rej_twice'] = { orders: st.orders, second: b, cart: st.cart };
}
// --- 10 daqiqa aniq "unknown" -> 'lost' (botga yetib bormadi), keyin "accepted" kelsa savat tozalanadi ---
{
  const byRef = (orders, id) => orders.find((o) => o.localId === id);
  const T0 = Date.parse('2026-11-21T10:00:00Z');
  const st = { orders: base(), cart: ['a'], clears: 0 };
  const steps = [];
  function unknown(atMs) {
    const r = R.noteUnknown(st.orders, 'p1', atMs);
    if (r) st.orders = r.orders;
    steps.push(r ? (r.lost ? 'lost' : 'since') : null);
  }
  unknown(T0);                       // birinchi "unknown" - vaqt eslab qolinadi
  unknown(T0 + 5 * 60 * 1000);       // 5 daqiqa - hali kutiladi
  unknown(T0 + 10 * 60 * 1000 - 1);  // 10 daqiqadan 1 ms kam
  unknown(T0 + 10 * 60 * 1000);      // 10 daqiqa - yetib bormadi
  unknown(T0 + 11 * 60 * 1000);      // 'lost' dan keyin o'zgarmaydi
  const lostOrder = byRef(st.orders, 'p1');
  out['lost'] = { steps, status: lostOrder.status, since: lostOrder.unknownSince, cart: st.cart.slice() };
  // bot baribir olgan ekan - "accepted": savat tozalanadi (takroriy buyurtma bo'lmasin), ikkinchi marta o'zgarmaydi
  const a = R.applyResult(st.orders, R.statusToResult('p1', { status: 'accepted', n: 12 }));
  st.orders = a.orders;
  if (a.accepted) { st.cart = []; st.clears += 1; }
  const b = R.applyResult(st.orders, R.statusToResult('p1', { status: 'accepted', n: 12 }));
  out['lost_then_ok'] = { accepted: a.accepted, second: b, status: byRef(st.orders, 'p1').status,
                          orderNo: byRef(st.orders, 'p1').orderNo, cart: st.cart, clears: st.clears };
  // boshqa holatlarga noteUnknown tegmaydi
  out['unknown_on_ok'] = R.noteUnknown(base(), 'o1', T0);
  out['unknown_bad'] = [R.noteUnknown(base(), 'zzz', T0), R.noteUnknown(null, 'p1', T0), R.noteUnknown(base(), 'p1', NaN)];
  out['lost_ms'] = R.LOST_AFTER_MS;
}
process.stdout.write(JSON.stringify(out));
"""


def run_harness() -> dict:
    tmp = Path(__file__).resolve().parent / "_result_harness.js"
    tmp.write_text(HARNESS, encoding="utf-8")
    try:
        proc = subprocess.run([NODE, str(tmp), str(MINIAPP / "result.js")], capture_output=True, text=True,
                              encoding="utf-8", timeout=30)
    finally:
        tmp.unlink(missing_ok=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def status(orders, local_id):
    return next(o for o in orders if o["localId"] == local_id)


def test_result_logic_idempotent():
    if NODE is None:
        print("SKIP: node topilmadi - result.js mantiqiy testi o'tkazib yuborildi")
        return
    r = run_harness()

    ok = r["ok_twice"]
    assert ok["changes"] == [True, False, False], "ikkinchi marta hech narsa o'zgarmaydi"
    assert status(ok["orders"], "p1")["status"] == "ok" and status(ok["orders"], "p1")["orderNo"] == "7"
    assert ok["cart"] == [] and ok["clears"] == 1, "savat faqat bir marta tozalanadi"
    assert len(ok["orders"]) == 3, "takror yozuv qo'shilmaydi"

    rej = r["rej_twice"]
    assert rej["changes"] == [True, False]
    assert status(rej["orders"], "p1")["status"] == "rejected" and status(rej["orders"], "p1")["reasonCode"] == "area"
    assert rej["cart"] == ["a"] and rej["clears"] == 0, "rad etilganda savat saqlanadi"

    cxp = r["cx_pending"]
    assert cxp["changes"] == [True, False]
    assert status(cxp["orders"], "p1")["status"] == "cancelled"
    assert cxp["cart"] == [] and cxp["clears"] == 1, "qabul qilinib bekor qilingan - savat tozalanadi"

    cxo = r["cx_ok"]
    assert cxo["changes"] == [True, False] and status(cxo["orders"], "o1")["status"] == "cancelled"
    assert cxo["clears"] == 0

    for name in ("unknown_ref", "ok_on_rejected", "ok_on_ok", "lang_only"):
        assert r[name]["changes"] == [False], name
        assert r[name]["clears"] == 0, name
    assert status(r["ok_on_ok"]["orders"], "o1")["orderNo"] == "3"

    assert status(r["bad_values"]["orders"], "p1")["reasonCode"] == "data"
    assert status(r["bad_n"]["orders"], "p1")["orderNo"] == ""

    both = r["ok_then_cx"]
    assert status(both["orders"], "p1")["status"] == "cancelled" and both["clears"] == 1
    assert r["input_untouched"] is True


def test_server_status_polling_logic():
    if NODE is None:
        print("SKIP: node topilmadi")
        return
    r = run_harness()
    m = r["status_map"]
    assert m["accepted"] == {"kind": "ok", "ref": "p1", "orderNo": "7"}
    assert m["accepted_bad_n"] == {"kind": "ok", "ref": "p1", "orderNo": ""}
    assert m["rejected"] == {"kind": "rej", "ref": "p1", "code": "area"}
    assert m["rejected_bad"] == {"kind": "rej", "ref": "p1", "code": "data"}
    assert m["cancelled"] == {"kind": "cx", "ref": "p1"}
    for key in ("unknown", "pending", "garbage", "no_ref"):
        assert m[key] is None, key
    # 4 s (2 daqiqagacha), keyin 10 s, 15 daqiqadan keyin to'xtaydi
    assert r["delays"] == [4000, 4000, 4000, 10000, 10000, 10000, None, None, 4000, 4000]
    pt = r["poll_twice"]
    assert pt["changes"] == [False, True, False, False, True]
    assert pt["clears"] == 1 and pt["cart"] == [], "savat bir marta tozalanadi"
    p1 = status(pt["orders"], "p1")
    assert p1["status"] == "cancelled" and p1["orderNo"] == "7" and len(pt["orders"]) == 3
    pr = r["poll_rej_twice"]
    assert pr["second"] is None and status(pr["orders"], "p1")["reasonCode"] == "limit" and pr["cart"] == ["a"]


def test_app_polls_order_status_quietly():
    code = strip_js_comments((MINIAPP / "app.js").read_text(encoding="utf-8"))
    for needle in ("/api/order-status?ref=", "Authorization: `tma ${initData}`", "credentials: 'omit'",
                   "'visibilitychange'", "RESULT.statusToResult(", "RESULT.pollDelay("):
        assert needle in code, needle
    init = code[code.index("async function init()"):]
    assert "startStatusPolling();" in init[:init.index("\n  }\n")], "ochilganda darhol so'raladi"
    start = code.index("const API_BASE = ")
    block = code[start:code.index("function reorder(")]
    for word in ("toast(", "haptic(", "alert", "T("):
        assert word not in block, f"polling indamasdan bo'lsin: {word}"
    # ?ok= / ?rej= / ?cx= zaxira yo'li saqlangan
    assert "applyResultObject(RESULT.parseResult(window.location.search))" in code



def test_unknown_for_10_minutes_unlocks_cart():
    if NODE is None:
        print("SKIP: node topilmadi")
        return
    r = run_harness()
    lost = r["lost"]
    assert lost["steps"] == ["since", None, None, "lost", None]
    assert lost["status"] == "lost" and lost["since"] == "2026-11-21T10:00:00.000Z"
    assert lost["cart"] == ["a"], "yetib bormagan buyurtma savati saqlanadi - qayta yuborish mumkin"
    lo = r["lost_then_ok"]
    assert lo["accepted"] is True and lo["second"] is None
    assert lo["status"] == "ok" and lo["orderNo"] == "12" and lo["cart"] == [] and lo["clears"] == 1
    assert r["unknown_on_ok"] is None and r["unknown_bad"] == [None, None, None]
    assert r["lost_ms"] == 10 * 60 * 1000


def test_order_ref_is_crypto_random_and_init_data_optional():
    code = strip_js_comments((MINIAPP / "app.js").read_text(encoding="utf-8"))
    fn = code[code.index("function newOrderRef()"):]
    fn = fn[:fn.index("\n  }\n")]
    assert "crypto.getRandomValues" in fn and "new Uint8Array(16)" in fn, "128 bit"
    assert "Math.random" not in code, "ref uchun Math.random ishlatilmasin"
    assert "const localId = newOrderRef();" in code
    enabled = code[code.index("function statusPollEnabled()"):]
    enabled = enabled[:enabled.index("\n  }\n")]
    assert "initData" not in enabled, "initData bo'sh bo'lsa ham so'rash ishlaydi"
    assert "headers: initData ? { Authorization: `tma ${initData}` } : {}" in code
    assert "resp.status === 401" not in code
    # 'lost' holati: savat ustidagi qator va tarixdagi yozuv
    assert "T('notice.lost')" in code and "T('status.lost')" in code
    d = load_miniapp_i18n()
    assert d["uz"]["notice.lost"] == "⚠️ Buyurtma botga yetib bormadi. Qayta yuboring."
    assert "не дошёл до бота" in d["ru"]["notice.lost"]


def test_config_api_base_allowed_by_csp():
    cfg = (MINIAPP / "config.js").read_text(encoding="utf-8")
    m = re.search(r"API_BASE:\s*'([^']*)'", cfg)
    assert m, "config.js da API_BASE"
    base = m.group(1).rstrip("/")
    html = (MINIAPP / "index.html").read_text(encoding="utf-8")
    csp = re.search(r"connect-src ([^;]+);", html).group(1).split()
    assert base and base in csp, "API_BASE CSP connect-src da bo'lishi kerak (aks holda brauzer bloklaydi)"
    assert set(csp) == {"'self'", base}, "boshqa tashqi manzil yo'q"
    assert re.fullmatch(r"https://[a-z0-9.-]+", base)


# ------------------------------------------------------------ indamasdan: xabar/banner yo'q

def _apply_fn_body() -> str:
    code = strip_js_comments((MINIAPP / "app.js").read_text(encoding="utf-8"))
    start = code.index("function applyBotResult()")
    end = code.index("\n  function ", start + 10)
    return code[start:end]


def test_apply_is_silent():
    body = _apply_fn_body()
    for word in ("toast(", "haptic(", "Notice", "alert", "T("):
        assert word not in body, word
    code = strip_js_comments((MINIAPP / "app.js").read_text(encoding="utf-8"))
    assert "pendingNotice" not in code and "openTab" not in code


def test_removed_keys_and_no_unused_keys():
    d = load_miniapp_i18n()
    for key in ("notice.rejected", "toast.accepted", "toast.accepted_n", "toast.cancelled", "toast.cancelled_n"):
        assert key not in d["uz"] and key not in d["ru"], key
    code = strip_js_comments((MINIAPP / "app.js").read_text(encoding="utf-8"))
    html = (MINIAPP / "index.html").read_text(encoding="utf-8")
    dynamic = re.compile(r"^reject\.")  # rejectReason(): T(`reject.${code}`)
    unused = []
    for key in d["uz"]:
        base = re.sub(r"_(one|few|many)$", "", key)
        if dynamic.match(key) or f"'{key}'" in code or f"'{base}'" in code or f'"{key}"' in html:
            continue
        unused.append(key)
    assert not unused, unused


def test_scripts_loaded_in_order_with_same_version():
    html = (MINIAPP / "index.html").read_text(encoding="utf-8")
    srcs = re.findall(r'<script src="(i18n|config|result|app)\.js\?v=(\d+)"', html)
    assert [s[0] for s in srcs] == ["i18n", "config", "result", "app"]
    versions = set(re.findall(r'\?v=(\d+)', html))
    assert len(versions) == 1 and int(versions.pop()) >= 14


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"OK    {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} test o'tdi")
    sys.exit(1 if failed else 0)

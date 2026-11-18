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
    srcs = re.findall(r'<script src="(i18n|result|app)\.js\?v=(\d+)"', html)
    assert [s[0] for s in srcs] == ["i18n", "result", "app"]
    versions = set(re.findall(r'\?v=(\d+)', html))
    assert len(versions) == 1 and int(versions.pop()) >= 12


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

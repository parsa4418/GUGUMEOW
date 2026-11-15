# -*- coding: utf-8 -*-
"""
API مینی‌اپ گیفت‌هاب — به بات فعلی وصل می‌شه، بدون دست زدن به کد گیفت/بانک.
در bot.py درست قبل از «# ================== Webhook / Render» اضافه کن:

    import miniapp_api
    miniapp_api.init(app, globals())

SQL (یک‌بار در Supabase):
  create table if not exists gift_listings (
    instance_id bigint primary key, seller_id bigint not null,
    price bigint not null check (price > 0), acquired_at text, created_at timestamptz not null default now());
  -- اگه جدول رو قبلاً ساخته بودی فقط این رو بزن:
  alter table gift_listings add column if not exists acquired_at text;
  create table if not exists rocket_bets (
    id bigserial primary key, user_id bigint not null, amount bigint not null,
    settled boolean not null default false, created_at timestamptz not null default now());
  alter table rocket_bets disable row level security;
  create table if not exists gift_market_log (
    id bigserial primary key, kind text not null, user_id bigint not null,
    gift_code bigint, gift_id bigint, price bigint, created_at timestamptz not null default now());
"""
import hashlib, hmac, json, os, gzip, time, logging, threading, math, random
from urllib.parse import parse_qsl
from urllib.request import urlopen
from flask import request, jsonify, Response, send_file

MARKET_FEE_RATE = 0.0          # کارمزد فروش (مثلاً 0.02 = ۲٪) از فروشنده کم می‌شه
MIN_PRICE, MAX_PRICE = 1_000, 100_000_000_000
_art, _names, _av = {}, {}, {}
# موشک‌ها: پک MeowieQ_StellarRockets؛ هر راند یکی‌شون تصادفی انتخاب می‌شه.
# فقط یه موشک ثابت می‌خوای؟ توی Render متغیر ROCKET_EMOJI_ID رو بذار. خالی کردن لیست = 🚀 معمولی.
ROCKETS = ["5870497334671976361", "5870559551568224733", "5870889353516951255", "5870506315448589899", "5872842481304870699", "5872888927081209304", "5870639274751172896", "5870876172262317485", "5870724289333829406", "5870823735006600994", "5870732810548945098", "5870723039498345368", "5870550815604744153", "5872739689852575759", "5870901826101981716", "5870690260307944550", "5870722704490897242", "5870520961287070072", "5870685759182217338", "5870931212268217669", "5870585132393440136", "5870685952455744603", "5870876455730161881", "5870958721533749076", "5870618289540964068", "5870669365292048004", "5870852678791209613", "5872770351624100932", "5870544940089482881", "5870838801751876665", "5870459594794346119", "5870592725895626826", "5870759301907226510", "5870885264708083615", "5870807676123882072", "5870644304157876866", "5870485703900535884", "5870462236199232445", "5870840085947097301", "5870851875632325259", "5870476405296340564", "5870476830498103731", "5870562094188864089", "5870820818723807767", "5870972276450533704", "5870955783776116644", "5870577302668059173", "5870956243337617042", "5870480008773907052", "5870781270664947166"]
if os.environ.get("ROCKET_EMOJI_ID"):
    ROCKETS = [os.environ["ROCKET_EMOJI_ID"]]
# ROCKET_ROT: اگه موشک کج بود، درجه‌ی چرخش رو توی Render بده؛ مثلاً -45 یا 45

ROCKET_ROT = float(os.environ.get("ROCKET_ROT", "0"))
# ایموجی‌های پرمیوم خود بات که توی ظاهر مینی‌اپ (آیکن‌ها و متن) استفاده می‌شن
UI_EMOJI = {5418261098291815736, 5832240029446971049, 5927029738626359516, 5843973755545590553,
            5938109560249127910, 5832433801191498078, 5888625992196431593, 5830144944399981619}
_lk = threading.Lock()


def init(app, N):
    sb = lambda: N["supabase"]
    TOKEN = N["BOT_TOKEN"]

    def auth():
        raw = request.headers.get("X-Init-Data", "")
        d = dict(parse_qsl(raw, keep_blank_values=True))
        h = d.pop("hash", "")
        chk = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        ok = hmac.compare_digest(hmac.new(key, chk.encode(), hashlib.sha256).hexdigest(), h)
        if not ok or time.time() - int(d.get("auth_date", 0)) > 86400:
            return None
        return json.loads(d["user"])

    def api(fn, check=True):
        def w(*a, **k):
            u = auth()
            if not u:
                return jsonify(ok=False, err="نشست معتبر نیست؛ مینی‌اپ را از داخل تلگرام باز کن."), 401
            uid = int(u["id"])
            if check and not N["get_user"](uid):
                return jsonify(ok=False, err="اول باید در بات /start بزنی."), 400
            try:
                return jsonify(fn(uid, u, request.get_json(silent=True) or {}))
            except Exception as e:
                logging.exception("miniapp %s: %s", fn.__name__, e)
                return jsonify(ok=False, err="خطای داخلی"), 500
        w.__name__ = fn.__name__
        return w

    def route(path, check=True):
        return lambda fn: app.route(path, methods=["POST"])(api(fn, check))

    def gift_row(g, price=None, seller=None):
        c = int(g["gift_code"])
        return {"id": int(g["instance_id"]), "tag": int(g.get("gift_id") or 0), "code": str(c),  # رشته: جاوااسکریپت اعداد ۱۹ رقمی رو گرد می‌کنه
                "type": N["_gift_type_name"](c), "model": N["_gift_model_name"](c),
                "value": int(g["current_value"]), "rarity": N["gift_rarity_name"](g["current_value"]),
                "bought": int(g.get("acquired_price") or g["current_value"]),
                "price": price, "seller": seller}

    def listings():
        """فقط لیست‌های معتبر: گیفت هنوز مال فروشنده باشه."""
        ls = sb().table("gift_listings").select("*").order("created_at", desc=True).execute().data or []
        if not ls:
            return []
        ids = [l["instance_id"] for l in ls]
        gs = {g["instance_id"]: g for g in sb().table("premium_gifts").select("*").in_("instance_id", ids).execute().data or []}
        return [(l, gs[l["instance_id"]]) for l in ls
                if l["instance_id"] in gs and gs[l["instance_id"]]["owner_id"] == l["seller_id"]
                and (gs[l["instance_id"]].get("acquired_at") or "") == (l.get("acquired_at") or "")]

    def uname(i):
        u = N["get_user"](i) or {}
        return u.get("username") or f"ID {i}"

    def log(kind, uid, g, price):
        try:
            sb().table("gift_market_log").insert({"kind": kind, "user_id": uid, "gift_code": int(g["gift_code"]),
                                                  "gift_id": int(g.get("gift_id") or 0), "price": price}).execute()
        except Exception:
            pass

    @app.route("/app")
    def mini_app():
        return send_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "miniapp", "index.html"))

    @app.route("/lottie.js")
    def lottie_js():
        # کتابخونه‌ی انیمیشن از سرور خودت سرو می‌شه تا CDN فیلتر/مسدود روی نمایش ایموجی اثر نذاره
        if "lottie" not in _art:
            for url in ("https://cdnjs.cloudflare.com/ajax/libs/lottie-web/5.12.2/lottie.min.js",
                        "https://cdn.jsdelivr.net/npm/lottie-web@5.12.2/build/player/lottie.min.js"):
                try:
                    _art["lottie"] = (urlopen(url, timeout=20).read(), "application/javascript")
                    break
                except Exception as e:
                    logging.error("lottie fetch %s: %s", url, e)
            else:
                return "", 502
        b, m = _art["lottie"]
        return Response(b, mimetype=m, headers={"Cache-Control": "public, max-age=604800"})

    @app.route("/api/art/<int:code>")
    def art(code):
        if code not in N["GIFT_MODEL_INDEX"] and code not in UI_EMOJI and str(code) not in ROCKETS:
            return "", 404
        thumb = bool(request.args.get("t"))
        key = (code, thumb)
        if key not in _art:
            try:
                st = N["bot"].get_custom_emoji_stickers([str(code)])[0]
                th = getattr(st, "thumbnail", None) or getattr(st, "thumb", None)
                if thumb and not th:
                    return "", 404          # بدون thumbnail؛ فرانت‌اند خودش فایل کامل رو می‌گیره
                p = N["bot"].get_file(th.file_id if thumb else st.file_id).file_path
                raw = urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{p}", timeout=15).read()
                if p.endswith(".tgs"):
                    _art[key] = (gzip.decompress(raw), "application/json")
                else:
                    _art[key] = (raw, "video/webm" if p.endswith(".webm") else "image/jpeg" if p.endswith(".jpg") else "image/webp")
            except Exception as e:
                logging.error("art %s: %s", code, e)
                return "", 404
        b, m = _art[key]
        return Response(b, mimetype=m, headers={"Cache-Control": "public, max-age=86400"})

    @route("/api/me")
    def me(uid, u, d):
        N["apply_bank_interest"](uid)
        usr = N["get_user"](uid)
        gs = N["get_user_gifts"](uid)
        ls = {l["instance_id"] for l, g in listings() if l["seller_id"] == uid}
        return {"ok": True, "id": uid, "name": u.get("first_name", ""), "username": usr.get("username"),
                "balance": int(usr.get("diamonds") or 0), "bank": int(usr.get("bank_balance") or 0),
                "gifts": len(gs), "worth": sum(int(g["current_value"]) for g in gs), "selling": len(ls),
                "slots": N["_gift_slot_capacity"](uid)}

    @route("/api/market")
    def market(uid, u, d):
        return {"ok": True, "items": [gift_row(g, int(l["price"]), uname(l["seller_id"]))
                                      | {"mine": l["seller_id"] == uid, "owner_id": int(l["seller_id"])}
                                      for l, g in listings()]}

    @route("/api/mine")
    def mine(uid, u, d):
        pr = {l["instance_id"]: int(l["price"]) for l, g in listings() if l["seller_id"] == uid}
        return {"ok": True, "items": [gift_row(g, pr.get(g["instance_id"])) | {"owner_id": uid}
                                      for g in N["get_user_gifts"](uid)]}

    @route("/api/owner")
    def owner(uid, u, d):
        i = int(d.get("id", 0))
        usr = N["get_user"](i)
        if not usr:
            return {"ok": False}
        if i not in _names:
            nm = ""
            try:
                nm = N["bot"].get_chat(i).first_name or ""
            except Exception:
                pass
            _names[i] = nm
        return {"ok": True, "id": i, "name": _names[i], "user": (usr.get("username") or "").lstrip("@")}

    @app.route("/api/avatar/<int:i>")
    def avatar(i):
        if i not in _av:
            try:
                if not N["get_user"](i):
                    return "", 404
                ph = N["bot"].get_user_profile_photos(i, limit=1)
                if not ph.total_count:
                    return "", 404
                p = N["bot"].get_file(ph.photos[0][0].file_id).file_path
                _av[i] = urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{p}", timeout=15).read()
            except Exception:
                return "", 404
        return Response(_av[i], mimetype="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})

    @route("/api/activity")
    def activity(uid, u, d):
        rows = sb().table("gift_market_log").select("*").eq("user_id", uid).order("id", desc=True).limit(30).execute().data or []
        return {"ok": True, "items": rows}

    @route("/api/list")
    def list_gift(uid, u, d):
        iid, price = int(d.get("id", 0)), int(d.get("price", 0))
        if not MIN_PRICE <= price <= MAX_PRICE:
            return {"ok": False, "err": f"قیمت باید بین {MIN_PRICE:,} و {MAX_PRICE:,} باشه."}
        if N["_marry_is_locked"](iid):
            return {"ok": False, "err": "این گیفت حلقه‌ی ازدواجته و قابل فروش نیست 💍"}
        g = sb().table("premium_gifts").select("*").eq("instance_id", iid).eq("owner_id", uid).execute().data
        if not g:
            return {"ok": False, "err": "این گیفت مال تو نیست."}
        sb().table("gift_listings").upsert({"instance_id": iid, "seller_id": uid, "price": price,
                                                  "acquired_at": g[0].get("acquired_at") or ""}).execute()
        log("list", uid, g[0], price)
        return {"ok": True}

    @route("/api/unlist")
    def unlist(uid, u, d):
        iid = int(d.get("id", 0))
        g = sb().table("premium_gifts").select("*").eq("instance_id", iid).eq("owner_id", uid).execute().data
        sb().table("gift_listings").delete().eq("instance_id", iid).eq("seller_id", uid).execute()
        if g:
            log("unlist", uid, g[0], 0)
        return {"ok": True}

    @route("/api/buy")
    def buy(uid, u, d):
        iid = int(d.get("id", 0))
        found = [(l, g) for l, g in listings() if l["instance_id"] == iid]
        if not found:
            return {"ok": False, "err": "این گیفت دیگه برای فروش نیست."}
        l, g = found[0]
        seller, price = int(l["seller_id"]), int(l["price"])
        if seller == uid:
            return {"ok": False, "err": "گیفت خودت رو نمی‌تونی بخری."}
        if int(d.get("price", price)) != price:
            return {"ok": False, "err": "قیمت عوض شده؛ صفحه رو تازه کن."}
        if len(N["get_user_gifts"](uid)) >= N["_gift_slot_capacity"](uid):
            return {"ok": False, "err": "جای خالی گیفت نداری. از بات «خرید جایگاه گیفت» بزن."}
        with N["_get_user_financial_lock"](uid):
            if N["get_balance"](uid) < price:
                return {"ok": False, "err": "موجودی کافی نیست."}
            if N["update_diamonds"](uid, -price) is None:
                return {"ok": False, "err": "پرداخت انجام نشد."}
            now = N["datetime"].now(N["TEHRAN_TZ"]).isoformat()
            up = sb().table("premium_gifts").update({"owner_id": uid, "active": False, "acquired_price": price,
                                                     "acquired_at": now}).eq("instance_id", iid).eq("owner_id", seller).execute()
            if not up.data:
                N["update_diamonds"](uid, price)
                return {"ok": False, "err": "همین الان یکی دیگه خریدش."}
        N["update_diamonds"](seller, price - int(price * MARKET_FEE_RATE))
        sb().table("gift_listings").delete().eq("instance_id", iid).execute()
        for x in (uid, seller):
            N["_invalidate_gift_display"](x)
        log("buy", uid, g, price)
        log("sold", seller, g, price)
        try:
            N["bot"].send_message(seller, f"🎁 گیفت #{g.get('gift_id')} در گیفت‌هاب به قیمت {price:,} اسکناس فروخته شد.")
        except Exception:
            pass
        return {"ok": True}

    @route("/api/bank")
    def bank(uid, u, d):
        N["apply_bank_interest"](uid)
        usr = N["get_user"](uid)
        bal = int(usr.get("bank_balance") or 0)
        acc = usr.get("bank_account_number")
        tx = N["_recent_bank_tx"](uid, 10) if acc else []
        return {"ok": True, "acc": acc, "bank": bal, "balance": int(usr.get("diamonds") or 0), "fee": N["BANK_OPENING_FEE"],
                "rate": N["BANK_INTEREST_RATE"], "daily": min(int(bal * N["BANK_INTEREST_RATE"]), N["BANK_DAILY_INTEREST_MAX"]),
                "tx": tx}

    @route("/api/bank/open")
    def bank_open(uid, u, d):
        ok, msg = N["open_bank_account"](uid)
        return {"ok": bool(ok), "err": None if ok else msg}

    @route("/api/bank/move")
    def bank_move(uid, u, d):
        kind, amt = d.get("kind"), int(d.get("amount", 0))
        if kind not in ("deposit", "withdraw") or amt <= 0:
            return {"ok": False, "err": "مبلغ نامعتبره."}
        with N["_get_user_financial_lock"](uid):
            if not N["get_user"](uid).get("bank_account_number"):
                return {"ok": False, "err": "اول حساب بانکی باز کن."}
            N["apply_bank_interest"](uid)
            if kind == "deposit":
                if N["get_balance"](uid) < amt:
                    return {"ok": False, "err": "موجودی کافی نیست."}
                if N["update_diamonds"](uid, -amt) is None:
                    return {"ok": False, "err": "انجام نشد."}
                if not N["change_bank_balance"](uid, amt):
                    N["update_diamonds"](uid, amt)
                    return {"ok": False, "err": "ثبت واریز انجام نشد."}
            else:
                if N["get_bank_balance"](uid) < amt:
                    return {"ok": False, "err": "موجودی بانک کافی نیست."}
                if not N["change_bank_balance"](uid, -amt):
                    return {"ok": False, "err": "ثبت برداشت انجام نشد."}
                if N["update_diamonds"](uid, amt) is None:
                    N["change_bank_balance"](uid, amt)
                    return {"ok": False, "err": "انجام نشد."}
        N["_record_bank_tx"](uid, kind, amt)
        return {"ok": True}


    # ================= بازی موشک (Crash) — فقط با ⟦پول⟧ بات =================
    K, WAIT, MIN_BET, MAX_BET, MAX_MULT = 0.12, 5.0, 1, 100_000_000, 250.0   # ضریب = e^(K*ثانیه)؛ ۵ ثانیه شرط‌بندی
    R = {"rocket": "", "id": 0, "phase": "wait", "wait_end": 0.0, "start": 0.0, "crash": 1.0, "bets": {}}
    hist, rl, bc, rnd = [], threading.Lock(), {}, random.SystemRandom()

    def gen():  # نقطه‌ی انفجار با ~۴٪ مزیت بازی
        return max(1.0, min(MAX_MULT, math.floor(96 / (1 - rnd.random())) / 100))

    def bg(fn, *a):
        threading.Thread(target=fn, args=a, daemon=True).start()

    def settle_db(bid):
        try:
            sb().table("rocket_bets").update({"settled": True}).eq("id", bid).execute()
        except Exception as e:
            logging.error("rocket settle: %s", e)

    def credit(uid, w):
        if N["update_diamonds"](uid, w) is None:
            logging.error("rocket credit failed uid=%s win=%s", uid, w)
        bc.pop(uid, None)

    def cash(uid, at):  # زیر قفل rl صدا زده می‌شه
        b = R["bets"][uid]
        b["at"], b["win"] = at, int(b["amount"] * at)
        bg(settle_db, b["bid"])
        bg(credit, uid, b["win"])
        return b["win"]

    def loop():
        while True:
            try:
                with rl:
                    R.update(rocket=rnd.choice(ROCKETS) if ROCKETS else "", id=R["id"] + 1, phase="wait", wait_end=time.time() + WAIT, crash=gen(), bets={})
                time.sleep(WAIT)
                with rl:
                    R.update(phase="fly", start=time.time())
                while True:
                    time.sleep(0.05)
                    with rl:
                        m = math.exp(K * (time.time() - R["start"]))
                        lim = min(m, R["crash"])
                        for uid, b in list(R["bets"].items()):
                            if not b["pend"] and not b["at"] and b["auto"] and b["auto"] <= lim and b["auto"] < R["crash"]:
                                cash(uid, b["auto"])
                        if m >= R["crash"]:
                            break
                with rl:
                    R["phase"] = "crash"
                    hist.insert(0, R["crash"])
                    del hist[20:]
                    for b in R["bets"].values():
                        if not b["pend"] and not b["at"]:
                            bg(settle_db, b["bid"])
                time.sleep(4)
            except Exception as e:
                logging.exception("rocket loop: %s", e)
                time.sleep(2)

    try:  # شرط‌های باز قبل از ری‌استارت سرور برگردونده می‌شه
        for b in sb().table("rocket_bets").select("*").eq("settled", False).execute().data or []:
            N["update_diamonds"](int(b["user_id"]), int(b["amount"]))
            settle_db(b["id"])
    except Exception as e:
        logging.error("rocket refund (جدول rocket_bets رو ساختی؟): %s", e)
    threading.Thread(target=loop, daemon=True).start()

    def bal(uid):
        c = bc.get(uid)
        if not c or time.time() - c[0] > 2:
            c = bc[uid] = (time.time(), N["get_balance"](uid))
        return c[1]

    @route("/api/rocket/state", check=False)
    def rk_state(uid, u, d):
        with rl:
            now, ph = time.time(), R["phase"]
            mine = R["bets"].get(uid)
            out = {"ok": True, "phase": ph, "left": max(0.0, R["wait_end"] - now) if ph == "wait" else 0,
                   "elapsed": now - R["start"] if ph == "fly" else 0, "crash": R["crash"] if ph == "crash" else None,
                   "hist": hist[:14], "round": R["id"], "rocket": R["rocket"], "rot": ROCKET_ROT,
                   "mine": {"amount": mine["amount"], "at": mine["at"], "win": mine["win"]} if mine and not mine["pend"] else None,
                   "bets": [{"id": k, "name": b["name"], "amount": b["amount"], "at": b["at"], "win": b["win"]}
                            for k, b in sorted(R["bets"].items(), key=lambda kv: -kv[1]["amount"]) if not b["pend"]][:30]}
        out["balance"] = bal(uid)
        return out

    @route("/api/rocket/bet")
    def rk_bet(uid, u, d):
        amt = int(d.get("amount", 0))
        auto = round(float(d.get("auto") or 0), 2)
        auto = auto if auto >= 1.01 else 0
        if not MIN_BET <= amt <= MAX_BET:
            return {"ok": False, "err": f"مبلغ شرط باید بین {MIN_BET:,} و {MAX_BET:,} باشه."}
        with rl:
            if R["phase"] != "wait" or R["wait_end"] - time.time() < 0.4:
                return {"ok": False, "err": "راند شروع شده؛ منتظر راند بعدی باش."}
            if uid in R["bets"]:
                return {"ok": False, "err": "توی این راند شرط بستی."}
            rid = R["id"]
            R["bets"][uid] = {"amount": amt, "auto": auto, "at": 0, "win": 0, "bid": 0, "pend": True,
                              "name": (u.get("first_name") or u.get("username") or "کاربر")[:14]}

        def drop():
            with rl:
                if R["id"] == rid and R["bets"].get(uid, {}).get("pend"):
                    del R["bets"][uid]
        try:
            with N["_get_user_financial_lock"](uid):
                if N["get_balance"](uid) < amt:
                    drop()
                    return {"ok": False, "err": "موجودی کافی نیست."}
                if N["update_diamonds"](uid, -amt) is None:
                    drop()
                    return {"ok": False, "err": "پرداخت انجام نشد."}
            bc.pop(uid, None)
            try:
                bid = sb().table("rocket_bets").insert({"user_id": uid, "amount": amt}).execute().data[0]["id"]
            except Exception as e:
                logging.error("rocket_bets insert: %s", e)
                N["update_diamonds"](uid, amt)
                bc.pop(uid, None)
                drop()
                return {"ok": False, "err": "خطای دیتابیس (جدول rocket_bets ساخته نشده؟)"}
            with rl:
                b = R["bets"].get(uid)
                if R["id"] == rid and R["phase"] == "wait" and b:
                    b["bid"], b["pend"] = bid, False
                    return {"ok": True}
            N["update_diamonds"](uid, amt)  # راند وسط ثبت شروع شد؛ برگردوندن پول
            bc.pop(uid, None)
            settle_db(bid)
            drop()
            return {"ok": False, "err": "راند شروع شد؛ پولت برگشت."}
        except Exception:
            drop()
            raise

    @route("/api/rocket/cash")
    def rk_cash(uid, u, d):
        with rl:
            b = R["bets"].get(uid)
            if R["phase"] != "fly" or not b or b["pend"] or b["at"]:
                return {"ok": False, "err": "الان نمی‌تونی برداشت کنی."}
            m = math.floor(math.exp(K * (time.time() - R["start"])) * 100) / 100
            if m >= R["crash"]:
                return {"ok": False, "err": "موشک منفجر شد 💥"}
            win = cash(uid, max(m, 1.0))
        return {"ok": True, "at": m, "win": win}

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
  create table if not exists gift_market_log (
    id bigserial primary key, kind text not null, user_id bigint not null,
    gift_code bigint, gift_id bigint, price bigint, created_at timestamptz not null default now());
"""
import hashlib, hmac, json, os, gzip, time, logging, threading
from urllib.parse import parse_qsl
from urllib.request import urlopen
from flask import request, jsonify, Response, send_file

MARKET_FEE_RATE = 0.0          # کارمزد فروش (مثلاً 0.02 = ۲٪) از فروشنده کم می‌شه
MIN_PRICE, MAX_PRICE = 1_000, 100_000_000_000
_art, _names, _av = {}, {}, {}
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

    def api(fn):
        def w(*a, **k):
            u = auth()
            if not u:
                return jsonify(ok=False, err="نشست معتبر نیست؛ مینی‌اپ را از داخل تلگرام باز کن."), 401
            uid = int(u["id"])
            if not N["get_user"](uid):
                return jsonify(ok=False, err="اول باید در بات /start بزنی."), 400
            try:
                return jsonify(fn(uid, u, request.get_json(silent=True) or {}))
            except Exception as e:
                logging.exception("miniapp %s: %s", fn.__name__, e)
                return jsonify(ok=False, err="خطای داخلی"), 500
        w.__name__ = fn.__name__
        return w

    def route(path):
        return lambda fn: app.route(path, methods=["POST"])(api(fn))

    def gift_row(g, price=None, seller=None):
        c = int(g["gift_code"])
        return {"id": int(g["instance_id"]), "tag": int(g.get("gift_id") or 0), "code": c,
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

    def _telegram_file(file_id):
        info = N["bot"].get_file(file_id)
        path = info.file_path
        data = urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=25).read()
        return path.lower(), data

    def _get_custom_emoji(code):
        # استفاده از متد کتابخانه؛ در نسخه‌های قدیمی‌تر، فراخوانی مستقیم Bot API.
        try:
            items = N["bot"].get_custom_emoji_stickers([str(code)])
        except Exception:
            from urllib.request import Request
            payload = json.dumps({"custom_emoji_ids": [str(code)]}).encode("utf-8")
            req = Request(
                f"https://api.telegram.org/bot{TOKEN}/getCustomEmojiStickers",
                data=payload, headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            if not result.get("ok"):
                raise RuntimeError(result.get("description", "Telegram API error"))
            items = result.get("result") or []
        if not items:
            raise RuntimeError("Telegram returned no sticker for this custom emoji")
        return items[0]

    @app.route("/api/art/<int:code>")
    def art(code):
        if code not in N["GIFT_MODEL_INDEX"]:
            return "", 404

        # ?thumb=1 تصویر ثابت بندانگشتی را برمی‌گرداند.
        thumb_only = request.args.get("thumb") == "1"
        cache_key = (int(code), bool(thumb_only))
        if cache_key not in _art:
            try:
                st = _get_custom_emoji(code)
                thumb = getattr(st, "thumbnail", None)
                candidates = []
                if thumb_only:
                    if thumb and getattr(thumb, "file_id", None):
                        candidates.append(thumb.file_id)
                    elif getattr(st, "file_id", None):
                        candidates.append(st.file_id)
                else:
                    if getattr(st, "file_id", None):
                        candidates.append(st.file_id)
                    if thumb and getattr(thumb, "file_id", None):
                        candidates.append(thumb.file_id)

                last_error = None
                for file_id in candidates:
                    try:
                        path, raw = _telegram_file(file_id)
                        if path.endswith(".tgs"):
                            data, mime = gzip.decompress(raw), "application/json"
                        elif path.endswith(".webm") or bool(getattr(st, "is_video", False)):
                            data, mime = raw, "video/webm"
                        else:
                            data, mime = raw, "image/webp"
                        _art[cache_key] = (data, mime)
                        break
                    except Exception as ex:
                        last_error = ex
                if cache_key not in _art:
                    raise last_error or RuntimeError("No downloadable sticker file")
            except Exception:
                logging.exception("gift art fetch failed (code=%s, thumb=%s)", code, thumb_only)
                return "", 404

        data, mime = _art[cache_key]
        return Response(data, mimetype=mime,
                        headers={"Cache-Control": "public, max-age=86400",
                                 "X-Content-Type-Options": "nosniff"})

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
            N["bot"].send_message(seller, f"🎁 گیفت #{g.get('gift_id')} در گیفت‌هاب به قیمت {price:,} 💎 فروخته شد.")
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

import os
import time
import sys
from datetime import datetime, timezone
import requests
import psycopg2
from psycopg2.extras import execute_values

DEX = "https://api.dexscreener.com"
TELEGRAM = "https://api.telegram.org/bot{}/sendMessage"

# 指定监控的 5 条链 (DexScreener 内部标识)
ALLOWED_CHAINS = {"ethereum", "solana", "bsc", "base", "robinhood"}

MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "30"))
MIN_ATH_MCAP = float(os.getenv("MIN_ATH_MCAP", "20000000"))
MAX_ATH_RATIO = float(os.getenv("MAX_ATH_RATIO", "0.30"))
MIN_LIQUIDITY = float(os.getenv("MIN_LIQUIDITY", "50000"))
MIN_VOLUME_24H = float(os.getenv("MIN_VOLUME_24H", "100000"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not DATABASE_URL:
    raise RuntimeError("缺少 DATABASE_URL（请连接 Railway PostgreSQL）")

HEADERS = {"User-Agent": "MemeScanner/Cloud/1.0"}

def db():
    return psycopg2.connect(DATABASE_URL, connect_timeout=15)

def init_db(conn):
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            chain_id TEXT NOT NULL,
            token_address TEXT NOT NULL,
            symbol TEXT,
            name TEXT,
            pair_address TEXT,
            pair_url TEXT,
            pair_created_at BIGINT,
            ath_market_cap DOUBLE PRECISION DEFAULT 0,
            ath_price DOUBLE PRECISION DEFAULT 0,
            ath_time TIMESTAMPTZ,
            current_market_cap DOUBLE PRECISION DEFAULT 0,
            current_price DOUBLE PRECISION DEFAULT 0,
            liquidity_usd DOUBLE PRECISION DEFAULT 0,
            volume_24h DOUBLE PRECISION DEFAULT 0,
            first_seen TIMESTAMPTZ DEFAULT NOW(),
            last_update TIMESTAMPTZ DEFAULT NOW(),
            alerted BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (chain_id, token_address)
        );
        """)
    conn.commit()

def get_json(url, timeout=20):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()

def discover():
    found = {}
    for endpoint in ["/token-profiles/latest/v1", "/token-boosts/latest/v1"]:
        try:
            data = get_json(DEX + endpoint)
            if isinstance(data, list):
                for x in data:
                    chain = str(x.get("chainId", "")).strip().lower()
                    addr = str(x.get("tokenAddress", "")).strip()
                    # 仅扫描指定的 5 条链
                    if chain in ALLOWED_CHAINS and addr:
                        found[(chain, addr)] = x
        except Exception as e:
            print(f"发现接口失败 {endpoint}: {e}", flush=True)
    return list(found.keys())

def batch(items, n=30):
    for i in range(0, len(items), n):
        yield items[i:i+n]

def fetch_pairs(keys):
    by_token = {}
    total = len(keys)
    done = 0
    for group in batch(keys, 30):
        by_chain = {}
        for chain, addr in group:
            by_chain.setdefault(chain, []).append(addr)

        for chain, addrs in by_chain.items():
            for sub in batch(addrs, 30):
                url = f"{DEX}/tokens/v1/{chain}/" + ",".join(sub)
                try:
                    data = get_json(url)
                    if isinstance(data, list):
                        for p in data:
                            token = str(p.get("baseToken", {}).get("address", "")).strip()
                            if not token:
                                continue
                            key = (chain, token)
                            liq = (p.get("liquidity") or {}).get("usd") or 0
                            vol = (p.get("volume") or {}).get("h24") or 0
                            old = by_token.get(key)
                            if old is None or float(liq or 0) > float(old.get("_liq", 0) or 0):
                                p["_liq"] = float(liq or 0)
                                p["_vol"] = float(vol or 0)
                                by_token[key] = p
                except Exception as e:
                    print(f"批量查询失败 chain={chain}: {e}", flush=True)
        done += len(group)
        print(f"已处理候选 {done}/{total}", flush=True)
    return by_token

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("未配置 Telegram 变量，跳过发送", flush=True)
        return
    url = TELEGRAM.format(BOT_TOKEN)
    r = requests.post(url, data={
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }, timeout=20)
    r.raise_for_status()

def fmt_money(x):
    x = float(x or 0)
    if x >= 1_000_000:
        return f"${x/1_000_000:.2f}M"
    if x >= 1_000:
        return f"${x/1_000:.1f}K"
    return f"${x:.0f}"

def process(conn, pairs):
    now = datetime.now(timezone.utc)
    alerts = []
    with conn.cursor() as cur:
        for key, p in pairs.items():
            chain, addr = key
            base = p.get("baseToken") or {}
            symbol = base.get("symbol") or "?"
            name = base.get("name") or "Unknown"
            price = float(p.get("priceUsd") or 0)
            mcap = float(p.get("marketCap") or p.get("fdv") or 0)
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            vol = float((p.get("volume") or {}).get("h24") or 0)
            created = p.get("pairCreatedAt")
            pair_url = p.get("url") or ""
            pair_addr = p.get("pairAddress") or ""

            if mcap <= 0 or price <= 0:
                continue

            age_days = None
            if created:
                try:
                    age_days = max(0, (time.time()*1000 - int(created)) / 86400000)
                except Exception:
                    pass

            cur.execute("""
                SELECT ath_market_cap, alerted, first_seen
                FROM tokens
                WHERE chain_id=%s AND token_address=%s
            """, (chain, addr))
            row = cur.fetchone()

            if row is None:
                ath = mcap
                alerted = False
                cur.execute("""
                    INSERT INTO tokens (
                        chain_id, token_address, symbol, name, pair_address, pair_url,
                        pair_created_at, ath_market_cap, ath_price, ath_time,
                        current_market_cap, current_price, liquidity_usd, volume_24h,
                        first_seen, last_update, alerted
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s,NOW(),NOW(),FALSE)
                """, (
                    chain, addr, symbol, name, pair_addr, pair_url, created,
                    ath, price, mcap, price, liq, vol
                ))
            else:
                old_ath = float(row[0] or 0)
                alerted = bool(row[1])
                ath = max(old_ath, mcap)
                cur.execute("""
                    UPDATE tokens SET
                        symbol=%s, name=%s, pair_address=%s, pair_url=%s,
                        pair_created_at=%s, ath_market_cap=%s, ath_price=%s,
                        current_market_cap=%s, current_price=%s,
                        liquidity_usd=%s, volume_24h=%s, last_update=NOW()
                    WHERE chain_id=%s AND token_address=%s
                """, (
                    symbol, name, pair_addr, pair_url, created,
                    ath, price if mcap >= old_ath else None,
                    mcap, price, liq, vol, chain, addr
                ))

            if age_days is None:
                cur.execute("""
                    SELECT EXTRACT(EPOCH FROM (NOW() - first_seen))/86400
                    FROM tokens WHERE chain_id=%s AND token_address=%s
                """, (chain, addr))
                fetch_res = cur.fetchone()
                age_days = float(fetch_res[0] or 0) if fetch_res else 0

            drawdown_ok = ath >= MIN_ATH_MCAP and mcap <= ath * MAX_ATH_RATIO
            other_ok = (
                age_days <= MAX_AGE_DAYS
                and liq >= MIN_LIQUIDITY
                and vol >= MIN_VOLUME_24H
            )

            if drawdown_ok and other_ok and not alerted:
                drawdown = (1 - mcap / ath) * 100 if ath else 0
                alerts.append({
                    "chain": chain, "symbol": symbol, "name": name,
                    "address": addr, "url": pair_url,
                    "ath": ath, "mcap": mcap, "drawdown": drawdown,
                    "liq": liq, "vol": vol, "age": age_days
                })
                cur.execute("""
                    UPDATE tokens SET alerted=TRUE
                    WHERE chain_id=%s AND token_address=%s
                """, (chain, addr))

    conn.commit()
    return alerts

def main():
    print("=== Meme Scanner 云端版：本次扫描开始 ===", flush=True)
    print(f"条件：年龄≤{MAX_AGE_DAYS}天 | ATH≥{fmt_money(MIN_ATH_MCAP)} | 回撤≥{(1-MAX_ATH_RATIO)*100:.0f}% | 流动性≥{fmt_money(MIN_LIQUIDITY)} | 24h量≥{fmt_money(MIN_VOLUME_24H)}", flush=True)

    conn = db()
    try:
        init_db(conn)
        keys = discover()
        print(f"发现候选 Token：{len(keys)}", flush=True)
        pairs = fetch_pairs(keys)
        print(f"拿到有效交易对：{len(pairs)}", flush=True)
        alerts = process(conn, pairs)
        print(f"本次命中：{len(alerts)}", flush=True)

        for a in alerts:
            msg = (
                f"🚨 Meme Scanner 命中\n"
                f"{a['name']} ({a['symbol']})\n"
                f"链：{a['chain']}\n"
                f"当前市值：{fmt_money(a['mcap'])}\n"
                f"记录到的历史最高市值：{fmt_money(a['ath'])}\n"
                f"从记录 ATH 回撤：{a['drawdown']:.1f}%\n"
                f"年龄约：{a['age']:.1f} 天\n"
                f"流动性：{fmt_money(a['liq'])}\n"
                f"24h成交量：{fmt_money(a['vol'])}\n"
                f"合约：{a['address']}\n"
                f"DexScreener：{a['url']}"
            )
            try:
                send_telegram(msg)
                print(f"Telegram 已发送：{a['symbol']}", flush=True)
            except Exception as e:
                print(f"Telegram 发送失败 {a['symbol']}: {e}", flush=True)
    finally:
        conn.close()
    print("=== 本次扫描结束 ===", flush=True)

if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as e:
            print(f"扫描过程发生捕获异常: {e}", flush=True)
        
        print("\n休眠 5 分钟后进行下一次扫描...\n", flush=True)
        time.sleep(300)

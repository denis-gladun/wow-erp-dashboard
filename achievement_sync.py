import os
import sys
import asyncio
import aiohttp
import requests
import gspread
import traceback
import time
from datetime import datetime, timezone, timedelta
from oauth2client.service_account import ServiceAccountCredentials

# ============================================================================
# ФИКС ДИРЕКТОРИИ
# ============================================================================
try:
    os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))
except:
    pass

# ============================================================================
# CONFIG MATRIX
# ============================================================================
CLIENT_ID = "ТУТ ЖИВЕТ КЛИЕНТ АЙДИ"
CLIENT_SECRET = "ТУТ ЖИВЕТ КЛИЕНТ СЕКРЕТ"

# Единый ID твоей таблицы
SPREADSHEET_URL = "ТУТ ЖИВЕТ ССЫЛКА НА ТАБЛИЦУ"

SHEET_INPUT_UI = "UI_Data"       
SHEET_OUTPUT_ILVL = "UI_Ilvl"    
SHEET_OUTPUT_GEAR = "UI_Gear"    
SHEET_RAID_CD = "Raid_CD"        

SERVICE_ACCOUNT_FILE = 'key.json' 

# Маппинг индексов для ILVL и GEAR
SLOT_MAP_ILVL = {
    "HEAD": 1, "NECK": 2, "SHOULDER": 3, "BACK": 4, "CHEST": 5, "WRIST": 6,
    "HANDS": 7, "WAIST": 8, "LEGS": 9, "FEET": 10, "FINGER_1": 11, "FINGER_2": 12,
    "TRINKET_1": 13, "TRINKET_2": 14, "MAIN_HAND": 15, "OFF_HAND": 16
}
CRAFTED_CORE_BONUSES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}

RAID_NAME_MAP = {
    "the voidspire": "voidspire",
    "the dreamrift": "dreamrift",
    "march on quel'danas": "march",
    "sporefall": "sporefall",
    "the sporefall": "sporefall",
}
RESET_RULES = {
    "us": {"weekday": 1, "hour": 15}, 
    "eu": {"weekday": 2, "hour": 4},  
}

CONCURRENCY_LIMIT = 10  

# ============================================================================
# СИНХРОННЫЕ УТИЛИТЫ И КЛИНИНГ
# ============================================================================
def get_blizz_token(region="us"):
    url = f"https://{region}.battle.net/oauth/token"
    resp = requests.post(url, auth=(CLIENT_ID, CLIENT_SECRET), data={"grant_type": "client_credentials"}, timeout=15)
    return resp.json()["access_token"]

def safe_str(data):
    if isinstance(data, dict): return data.get("en_US", "")
    return str(data) if data else ""

def clean_enchant_string(raw_str):
    if not raw_str: return ""
    if raw_str.startswith("Enchanted: "): raw_str = raw_str.replace("Enchanted: ", "", 1)
    if "|" in raw_str: raw_str = raw_str.split("|")[0]
    return raw_str.strip()

def get_gem_data(item, socket_index):
    sockets = item.get("sockets", [])
    if len(sockets) > socket_index:
        s = sockets[socket_index]
        return safe_str(s.get("item", {}).get("name", "")), safe_str(s.get("display_string", ""))
    return "", ""

def get_reset_ms(region):
    rule = RESET_RULES.get(region.lower(), RESET_RULES["us"])
    now = datetime.now(timezone.utc)
    reset = now.replace(hour=rule["hour"], minute=0, second=0, microsecond=0)
    days_back = (now.weekday() - rule["weekday"]) % 7
    reset -= timedelta(days=days_back)
    if now < reset: reset -= timedelta(days=7)
    return int(reset.timestamp() * 1000)

# ============================================================================
# АСИНХРОННЫЕ ВОРКЕРЫ: ПОТОК UI_DATA (ILVL + GEAR)
# ============================================================================
async def fetch_ui_character(session, semaphore, row, token, index):
    async with semaphore:
        name = row[4]
        realm = row[3].lower().replace(" ", "-").replace("'", "")
        reg = row[1].lower()
        slug = row[5].lower().replace(" ", "-") if (len(row) > 5 and row[5].strip()) else name.lower().replace(" ", "-")

        headers = {"Authorization": f"Bearer {token}"}
        params = {"namespace": f"profile-{reg}", "locale": "en_US"}
        
        eq_url = f"https://{reg}.api.blizzard.com/profile/wow/character/{realm}/{slug}/equipment"
        prof_url = f"https://{reg}.api.blizzard.com/profile/wow/character/{realm}/{slug}"
        
        print(f"📦 [UI Воркер {index}] Запрос: {name} ({reg.upper()}-{realm})")
        try:
            async def fetch_json(url):
                async with session.get(url, params=params, headers=headers, timeout=15) as resp:
                    return (await resp.json()) if resp.status == 200 else None

            eq_data, prof_data = await asyncio.gather(fetch_json(eq_url), fetch_json(prof_url))
            if not eq_data: return None

            # --- СБОРКА ДЛЯ UI_ILVL ---
            ilvl_row = [""] * 22
            ilvl_row[0] = name
            for item in eq_data.get("equipped_items", []):
                slot = item.get("slot", {}).get("type")
                lvl = item.get("level", {}).get("value")
                if slot in SLOT_MAP_ILVL: ilvl_row[SLOT_MAP_ILVL[slot]] = lvl
            if prof_data: ilvl_row[17] = prof_data.get("equipped_item_level", "")
            ilvl_row[18], ilvl_row[19] = row[3], reg.upper()
            ilvl_row[20] = f"{reg}|{realm}|{name.lower()}"
            ilvl_row[21] = f"{name} • {row[3]} ({reg.upper()})"

            # --- СБОРКА ДЛЯ UI_GEAR ---
            gear_row = [""] * 64
            gear_row[0], gear_row[1] = name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            items_dict = {item.get("slot", {}).get("type"): item for item in eq_data.get("equipped_items", [])}
            
            # Тьер-сеты
            tier_slots, tier_count = ["HEAD", "SHOULDER", "CHEST", "HANDS", "LEGS"], 0
            for i, slot in enumerate(tier_slots):
                if "set" in items_dict.get(slot, {}):
                    gear_row[2+i] = "Yes"; tier_count += 1
            gear_row[7] = tier_count

            # Чарки
            ench_slots = ["HEAD", "SHOULDER", "CHEST", "LEGS", "FEET", "FINGER_1", "FINGER_2", "MAIN_HAND", "OFF_HAND"]
            for i, slot in enumerate(ench_slots):
                enchants = items_dict.get(slot, {}).get("enchantments", [])
                if enchants: gear_row[8+i] = clean_enchant_string(safe_str(enchants[0].get("display_string", "")))

            # Оффхенд
            gear_row[17] = "Yes" if safe_str(items_dict.get("OFF_HAND", {}).get("item_class", {}).get("name", "")) == "Weapon" else "No"

            # Слоты 1 + Гемы
            sock1_slots = ["HEAD", "WRIST", "WAIST", "NECK", "FINGER_1", "FINGER_2"]
            for i, slot in enumerate(sock1_slots):
                sockets = items_dict.get(slot, {}).get("sockets", [])
                if slot in ["HEAD", "WRIST", "WAIST"]:
                    gear_row[18+i] = "FILLED_SOCKET" if (sockets and sockets[0].get("item")) else ("EMPTY_SOCKET" if sockets else "NO_SOCKET")
                else:
                    gear_row[18+i] = "FILLED_SOCKET" if (sockets and sockets[0].get("item")) else "EMPTY_SOCKET"
                g_name, g_stats = get_gem_data(items_dict.get(slot, {}), 0)
                gear_row[24+i], gear_row[30+i] = g_name, g_stats

            # Украшения (Embellishments)
            emb_slots, emb_count = ["HEAD", "NECK", "SHOULDER", "CHEST", "BACK", "WRIST", "HANDS", "WAIST", "LEGS", "FEET", "FINGER_1", "FINGER_2", "MAIN_HAND", "OFF_HAND"], 0
            for i, slot in enumerate(emb_slots):
                bonuses = items_dict.get(slot, {}).get("bonus_list", [])
                if any(b in CRAFTED_CORE_BONUSES for b in bonuses) or "Embellished" in items_dict.get(slot, {}).get("limit_category", ""):
                    gear_row[36+i] = "Yes"; emb_count += 1
            gear_row[50] = emb_count
            gear_row[51], gear_row[52] = row[3], reg.upper()
            gear_row[53] = f"{reg}|{realm}|{name.lower()}"
            gear_row[54] = f"{name} • {row[3]} ({reg.upper()})"

            # Слоты 2 + Гемы
            sock2_slots = ["NECK", "FINGER_1", "FINGER_2"]
            for i, slot in enumerate(sock2_slots):
                sockets = items_dict.get(slot, {}).get("sockets", [])
                gear_row[55+i] = "FILLED_SOCKET" if (len(sockets) > 1 and sockets[1].get("item")) else ("EMPTY_SOCKET" if len(sockets) > 1 else "NO_SOCKET")
                g_name2, g_stats2 = get_gem_data(items_dict.get(slot, {}), 1)
                gear_row[58+i], gear_row[61+i] = g_name2, g_stats2

            return {"ilvl": ilvl_row, "gear": gear_row}
        except Exception as e:
            print(f"❌ Ошибка UI парсинга персонажа {name}: {e}")
            return None

# ============================================================================
# АСИНХРОННЫЕ ВОРКЕРЫ: ПОТОК RAID COOLDOWNS
# ============================================================================
async def fetch_raid_cd(session, semaphore, row, row_num, tokens):
    async with semaphore:
        char_name = str(row[17]).strip()  # Col R
        region = str(row[18]).strip().lower()  # Col S
        realm = str(row[19]).strip()  # Col T
        
        if not char_name or region not in tokens: return None
        token = tokens[region]
        realm_slug = realm.lower().replace(" ", "-").replace("'", "")
        
        url = f"https://{region}.api.blizzard.com/profile/wow/character/{realm_slug}/{char_name.lower()}/encounters/raids"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"namespace": f"profile-{region}", "locale": "en_US"}
        
        print(f"⚔️ [Raid Воркер] Запрос: {char_name} ({region.upper()}-{realm})")
        try:
            async with session.get(url, headers=headers, params=params, timeout=15) as resp:
                if resp.status != 200: return None
                data = await resp.json()
                
            reset_ms = get_reset_ms(region)
            kills = { ("voidspire", "N"): 0, ("voidspire", "H"): 0, ("voidspire", "M"): 0,
                      ("march", "N"): 0, ("march", "H"): 0, ("march", "M"): 0,
                      ("dreamrift", "N"): 0, ("dreamrift", "H"): 0, ("dreamrift", "M"): 0,
                      ("sporefall", "N"): 0, ("sporefall", "H"): 0, ("sporefall", "M"): 0 }

            for exp in data.get("expansions", []):
                for inst in exp.get("instances", []):
                    r_key = RAID_NAME_MAP.get(inst["instance"]["name"].lower())
                    if not r_key: continue
                    
                    for mode in inst.get("modes", []):
                        m_type = mode["difficulty"]["type"][0]
                        count = 0
                        for enc in mode.get("progress", {}).get("encounters", []):
                            if enc.get("last_kill_timestamp", 0) >= reset_ms: count += 1
                        kills[(r_key, m_type)] = count

            # 🚀 ТОЧКА ИЗМЕНЕНИЯ: Перестроили массив. Теперь Sporefall идет строго ПОСЛЕ Last_Sync и Char_Label
            upd_row = [
                kills[("voidspire", "N")], kills[("voidspire", "H")], kills[("voidspire", "M")], # U, V, W
                kills[("march", "N")], kills[("march", "H")], kills[("march", "M")],             # X, Y, Z
                kills[("dreamrift", "N")], kills[("dreamrift", "H")], kills[("dreamrift", "M")], # AA, AB, AC
                datetime.now().strftime("%d.%m %H:%M"),                                          # AD (Last_Sync)
                f"{char_name} • {realm} ({region.upper()})",                                     # AE (Char_Label)
                kills[("sporefall", "N")], kills[("sporefall", "H")], kills[("sporefall", "M")]  # AF, AG, AH (SP_N, SP_H, SP_M)
            ]
            return {"row_num": row_num, "data": upd_row}
        except Exception as e:
            print(f"❌ Ошибка рейдового парсинга {char_name}: {e}")
            return None

# ============================================================================
# ГЛАВНЫЙ ОРКЕСТРАТОР ДВИЖКА
# ============================================================================
async def run_pipeline():
    start_time = time.time()
    print("--- ЗАПУСК ЕДИНОГО АСИНХРОННОГО КОМБАЙНА КИРЫ ---")
    
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"🚨 ОШИБКА: Файл {SERVICE_ACCOUNT_FILE} не найден!")
        return

    print("Авторизация в Google Sheets API...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
    g_client = gspread.authorize(creds)
    
    print("Открытие рабочих листов...")
    ss = g_client.open_by_url(SPREADSHEET_URL)
    ws_ui_data = ss.worksheet(SHEET_INPUT_UI)
    ws_ui_ilvl = ss.worksheet(SHEET_OUTPUT_ILVL)
    ws_ui_gear = ss.worksheet(SHEET_OUTPUT_GEAR)
    ws_raid_cd = ss.worksheet(SHEET_RAID_CD)

    print("Сканирование и импорт исходных матриц...")
    ui_raw_values = ws_ui_data.get_all_values()
    raid_raw_values = ws_raid_cd.get_all_values()

    print("Генерация токенов авторизации Blizzard API...")
    tokens = {"us": get_blizz_token("us"), "eu": get_blizz_token("eu")}

    # --- СОРТИРОВКА И ПОДГОТОВКА СТРОК UI_DATA ---
    in_marker_idx = None
    for i, row in enumerate(ui_raw_values):
        if "BLIZZ API INPUTS" in " ".join(row):
            in_marker_idx = i; break
    start_r_ui = (in_marker_idx + 2) if in_marker_idx is not None else 2

    valid_ui_rows = []
    for r_idx in range(start_r_ui, len(ui_raw_values)):
        row = ui_raw_values[r_idx]
        if len(row) < 6 or not any(row): break
        if row[0].lower() in ["true", "1", "yes", "истина"]:
            valid_ui_rows.append(row)

    # ============================================================================
    # АСИНХРОННЫЙ СЕТЕВОЙ КОНВЕЙЕР
    # ============================================================================
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
    connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        ui_tasks = []
        for idx, row in enumerate(valid_ui_rows, start=1):
            ui_tasks.append(fetch_ui_character(session, semaphore, row, tokens["us"], idx))
            
        raid_tasks = []
        for i in range(4, len(raid_raw_values)):
            raid_tasks.append(fetch_raid_cd(session, semaphore, raid_raw_values[i], i + 1, tokens))

        print(f"🚀 Запуск параллельного штурма API ({len(ui_tasks)} UI чаров, {len(raid_tasks)} рейд-строк)...")
        ui_results, raid_results = await asyncio.gather(
            asyncio.gather(*ui_tasks),
            asyncio.gather(*raid_tasks)
        )

    # ============================================================================
    # ПАКЕТНАЯ ЗАПИСЬ РЕЗУЛЬТАТОВ (BATCH UPDATES)
    # ============================================================================
    print("Сетевые операции завершены. Начинаю пакетный экспорт в Таблицы...")

    # 1. Запись в UI_Ilvl
    ilvl_clean = [r["ilvl"] for r in ui_results if r is not None]
    if ilvl_clean:
        ws_ui_ilvl.batch_clear([f"A2:V1000"])
        ws_ui_ilvl.update(range_name=f"A2:V{2 + len(ilvl_clean) - 1}", values=ilvl_clean)
        print(f"✅ Лист {SHEET_OUTPUT_ILVL} успешно обновлен пачкой ({len(ilvl_clean)} строк).")

    # 2. Запись в UI_Gear
    gear_clean = [r["gear"] for r in ui_results if r is not None]
    if gear_clean:
        needed_rows = 3 + len(gear_clean) + 150
        if ws_ui_gear.row_count < needed_rows:
            ws_ui_gear.resize(rows=needed_rows, cols=70)
        
        pad_rows = 150 - len(gear_clean)
        if pad_rows > 0:
            gear_clean.extend([[""] * 64 for _ in range(pad_rows)])
            
        ws_ui_gear.update(range_name=f"A3:BL{3 + len(gear_clean) - 1}", values=gear_clean)
        print(f"✅ Лист {SHEET_OUTPUT_GEAR} успешно обновлен пачкой ({len(gear_clean)} строк).")

    # 3. Запись в Raid_CD (Ювелирный BatchUpdate без перезаписи всей таблицы)
    raid_clean = [r for r in raid_results if r is not None]
    if raid_clean:
        batch_payload = []
        for r in raid_clean:
            batch_payload.append({
                # Диапазон U:AH покрывает все 14 колонок от Voidspire до новых ячеек Sporefall включительно
                "range": f"U{r['row_num']}:AH{r['row_num']}",
                "values": [r["data"]]
            })
        ws_raid_cd.batch_update(batch_payload)
        print(f"✅ Лист {SHEET_RAID_CD} успешно обновлен точечным батчем ({len(raid_clean)} строк).")

    print(f"🏁 СИСТЕМА ОБНОВЛЕНА НА ТУРБО-СКОРОСТИ ЗА {round(time.time() - start_time, 2)} СЕКУНД!")

def main():
    try:
        asyncio.run(run_pipeline())
    except Exception:
        traceback.print_exc()
    input("\nНажми ENTER для выхода...")

if __name__ == "__main__":
    main()

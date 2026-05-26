import os
import sys
import requests
import gspread
import traceback
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

# ============================================================================
# ФИКС ДИРЕКТОРИИ
# ============================================================================
try:
    os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))
except:
    pass

# =========================
# CONFIG
# =========================
CLIENT_ID = "ТУТ ЖИВЕТ ID"
CLIENT_SECRET = "ТУТ ЖИВЕТ SECRET"

SHEET_URL = "ТУТ ЖИВЕТ ССЫЛКА НА ТАБЛИЦУ"
SHEET_INPUT = "UI_Data"       # Откуда берем список персонажей
SHEET_OUTPUT = "UI_Gear"      # Куда выгружаем подробный ГИР
SERVICE_ACCOUNT_FILE = 'key.json' 

OUTPUT_START_ROW = 3          # Начинаем писать с 3-й строки

INPUT_START_ROW = 3
COL_USE = 1            # A
COL_REGION = 2         # B
COL_REALM_SLUG = 4     # D
COL_CHAR_NAME = 5      # E
COL_CHAR_SLUG = 6      # F

CRAFTED_CORE_BONUSES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}

# =========================
# FUNCTIONS
# =========================

def get_token():
    url = "https://oauth.battle.net/token"
    resp = requests.post(url, auth=(CLIENT_ID, CLIENT_SECRET), data={"grant_type": "client_credentials"})
    return resp.json()["access_token"]

def safe_str(data):
    """Бронебойная защита от кривых локализаций"""
    if isinstance(data, dict):
        return data.get("en_US", "")
    return str(data) if data else ""

def clean_enchant_string(raw_str):
    """Очищает строку чарки от мусора Близзов (иконок и префиксов)"""
    if not raw_str:
        return ""
    if raw_str.startswith("Enchanted: "):
        raw_str = raw_str.replace("Enchanted: ", "", 1)
    if "|" in raw_str:
        raw_str = raw_str.split("|")[0]
    return raw_str.strip()

def get_gem_data(item, socket_index):
    sockets = item.get("sockets", [])
    if len(sockets) > socket_index:
        s = sockets[socket_index]
        name = safe_str(s.get("item", {}).get("name", ""))
        stats = safe_str(s.get("display_string", ""))
        return name, stats
    return "", ""

def main():
    print(f"--- ЗАПУСК СБОРА ГИРА (ВЫГРУЗКА В {SHEET_OUTPUT}, СТРОКА {OUTPUT_START_ROW}) ---")
    
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"🚨 ОШИБКА: Файл {SERVICE_ACCOUNT_FILE} не найден в папке {os.getcwd()}")
        return

    print("Авторизация в Google...")
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
    client = gspread.authorize(creds)
    
    print("Открываем таблицу...")
    wb = client.open_by_url(SHEET_URL)
    ws_in = wb.worksheet(SHEET_INPUT)
    ws_out = wb.worksheet(SHEET_OUTPUT)

    print(f"Загрузка входных данных из {SHEET_INPUT}...")
    all_rows = ws_in.get_all_values()
    token = get_token()
    output_data = []

    for r_idx in range(INPUT_START_ROW - 1, len(all_rows)):
        row = all_rows[r_idx]
        
        while len(row) < 10:
            row.append("")

        use = row[COL_USE-1].lower()
        name = row[COL_CHAR_NAME-1]
        
        if not use and not name:
            continue

        if use in ["true", "1", "yes", "истина"]:
            realm = row[COL_REALM_SLUG-1].lower()
            reg = row[COL_REGION-1].lower()
            slug = row[COL_CHAR_SLUG-1].lower() or name.lower()

            print(f"Парсим ГИР: {name}...")
            url = f"https://{reg}.api.blizzard.com/profile/wow/character/{realm}/{slug}/equipment"
            headers = {"Authorization": f"Bearer {token}"}
            resp = requests.get(url, params={"namespace": f"profile-{reg}", "locale": "en_US"}, headers=headers, timeout=20)
            
            if resp.status_code == 200:
                payload = resp.json()
                items = {item.get("slot", {}).get("type"): item for item in payload.get("equipped_items", [])}
                
                out = [""] * 64
                
                out[0] = name
                out[1] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                tier_slots = ["HEAD", "SHOULDER", "CHEST", "HANDS", "LEGS"]
                tier_count = 0
                for i, slot in enumerate(tier_slots):
                    if "set" in items.get(slot, {}):
                        out[2+i] = "Yes"
                        tier_count += 1
                out[7] = tier_count

                ench_slots = ["HEAD", "SHOULDER", "CHEST", "LEGS", "FEET", "FINGER_1", "FINGER_2", "MAIN_HAND", "OFF_HAND"]
                for i, slot in enumerate(ench_slots):
                    enchants = items.get(slot, {}).get("enchantments", [])
                    if enchants:
                        raw_enchant = safe_str(enchants[0].get("display_string", ""))
                        out[8+i] = clean_enchant_string(raw_enchant)

                # Логика оффхенда
                oh_class = items.get("OFF_HAND", {}).get("item_class", {}).get("name", "")
                out[17] = "Yes" if safe_str(oh_class) == "Weapon" else "No"

                # 18-23: Sockets 1 Status + Gems
                sock1_slots = ["HEAD", "WRIST", "WAIST", "NECK", "FINGER_1", "FINGER_2"]
                for i, slot in enumerate(sock1_slots):
                    sockets = items.get(slot, {}).get("sockets", [])
                    
                    if slot in ["HEAD", "WRIST", "WAIST"]:
                        if len(sockets) > 0:
                            out[18+i] = "FILLED_SOCKET" if sockets[0].get("item") else "EMPTY_SOCKET"
                        else:
                            out[18+i] = "NO_SOCKET"
                    else:
                        if len(sockets) > 0:
                            out[18+i] = "FILLED_SOCKET" if sockets[0].get("item") else "EMPTY_SOCKET"
                        else:
                            out[18+i] = "EMPTY_SOCKET"

                    name_gem, stats_gem = get_gem_data(items.get(slot, {}), 0)
                    out[24+i] = name_gem
                    out[30+i] = stats_gem

                emb_slots = ["HEAD", "NECK", "SHOULDER", "CHEST", "BACK", "WRIST", "HANDS", "WAIST", "LEGS", "FEET", "FINGER_1", "FINGER_2", "MAIN_HAND", "OFF_HAND"]
                emb_count = 0
                for i, slot in enumerate(emb_slots):
                    bonuses = items.get(slot, {}).get("bonus_list", [])
                    is_emb = any(b in CRAFTED_CORE_BONUSES for b in bonuses)
                    limit_cat = items.get(slot, {}).get("limit_category", "")
                    if is_emb or "Embellished" in limit_cat:
                        out[36+i] = "Yes"
                        emb_count += 1
                
                out[50] = emb_count
                out[51] = row[COL_REALM_SLUG-1]
                out[52] = reg.upper()
                out[53] = f"{reg}|{row[COL_REALM_SLUG-1]}|{name.lower()}"
                out[54] = f"{name} • {row[COL_REALM_SLUG-1]} ({reg.upper()})"

                # 55-57: Sockets 2 Status + Gems
                sock2_slots = ["NECK", "FINGER_1", "FINGER_2"]
                for i, slot in enumerate(sock2_slots):
                    sockets = items.get(slot, {}).get("sockets", [])
                    if len(sockets) > 1:
                        out[55+i] = "FILLED_SOCKET" if sockets[1].get("item") else "EMPTY_SOCKET"
                    else:
                        out[55+i] = "NO_SOCKET"
                        
                    name_gem2, stats_gem2 = get_gem_data(items.get(slot, {}), 1)
                    out[58+i] = name_gem2
                    out[61+i] = stats_gem2

                output_data.append(out)
            else:
                print(f"  ! Ошибка API для {name}: {resp.status_code}")

    if output_data:
        print(f"Записываю {len(output_data)} строк гира в лист {SHEET_OUTPUT}...")
        
        # Проверяем лимиты целевого листа UI_Gear, а не настроечного
        needed_rows = OUTPUT_START_ROW + len(output_data) + 150
        if ws_out.col_count < 70 or ws_out.row_count < needed_rows:
            ws_out.resize(rows=max(ws_out.row_count, needed_rows), cols=max(ws_out.col_count, 70))
            
        # Паддинг для затирания старых хвостов
        pad_rows = 150 - len(output_data)
        if pad_rows > 0:
            output_data.extend([[""] * 64 for _ in range(pad_rows)])
            
        update_range = f"A{OUTPUT_START_ROW}:BL{OUTPUT_START_ROW + len(output_data) - 1}"
        ws_out.update(update_range, output_data)
        print(f"✅ ГИР ГОТОВ! Данные сохранены в лист '{SHEET_OUTPUT}'.")
    else:
        print("🤷‍♀️ Не нашла ни одного персонажа с галочкой 'Use'.")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
    input("\nНажми Enter для выхода...")
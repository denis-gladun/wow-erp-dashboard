import os
import sys
import requests
import gspread
import traceback
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
SHEET_INPUT = "UI_Data"       # Откуда берем настройки и список чаров
SHEET_OUTPUT = "UI_Ilvl"      # Куда выгружаем результаты API
SERVICE_ACCOUNT_FILE = 'key.json' 

# ГРАНИЦЫ БЛОКА НА ВЫГРУЗКУ (В листе UI_Ilvl)
OUTPUT_START_ROW = 2 
OUTPUT_LIMIT_ROW = 1000 # С запасом для чистого листа

# КАРТА СЛОТОВ
SLOT_MAP = {
    "HEAD": 1, "NECK": 2, "SHOULDER": 3, "BACK": 4, "CHEST": 5, "WRIST": 6,
    "HANDS": 7, "WAIST": 8, "LEGS": 9, "FEET": 10, "FINGER_1": 11, "FINGER_2": 12,
    "TRINKET_1": 13, "TRINKET_2": 14, "MAIN_HAND": 15, "OFF_HAND": 16
}

AVG_ILVL_IDX = 17   # Equipped_iLvl
REALM_IDX = 18      # Realm
REGION_IDX = 19     # Region
KEY_IDX = 20        # Char_Key
LABEL_IDX = 21      # Char_Label

# =========================
# FUNCTIONS
# =========================

def get_token():
    url = "https://oauth.battle.net/token"
    resp = requests.post(url, auth=(CLIENT_ID, CLIENT_SECRET), data={"grant_type": "client_credentials"})
    return resp.json()["access_token"]

def main():
    print(f"--- ЗАПУСК (ВЫГРУЗКА В {SHEET_OUTPUT}, СТРОКИ: {OUTPUT_START_ROW}-{OUTPUT_LIMIT_ROW}) ---")
    
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"🚨 ОШИБКА: Файл {SERVICE_ACCOUNT_FILE} не найден!")
        return

    print("Авторизация в Google...")
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, scope)
    client = gspread.authorize(creds)
    
    print("Открытие таблицы...")
    wb = client.open_by_url(SHEET_URL)
    ws_in = wb.worksheet(SHEET_INPUT)
    ws_out = wb.worksheet(SHEET_OUTPUT)

    print(f"Загрузка входных данных из {SHEET_INPUT}...")
    all_values = ws_in.get_all_values()
    
    in_marker_idx = None
    for i, row in enumerate(all_values):
        if "BLIZZ API INPUTS" in " ".join(row):
            in_marker_idx = i
            break
            
    if in_marker_idx is None:
        print(f"🚨 ОШИБКА: Маркер 'BLIZZ API INPUTS' не найден в листе {SHEET_INPUT}.")
        return

    token = get_token()
    results_to_write = []
    updated_count = 0
    
    # Максимальное кол-во строк для записи на целевом листе
    max_capacity = OUTPUT_LIMIT_ROW - OUTPUT_START_ROW + 1

    for r_idx in range(in_marker_idx + 2, len(all_values)):
        row = all_values[r_idx]
        if len(row) < 6 or not any(row): break

        use = row[0].lower()
        if use in ["true", "1", "yes", "истина"]:
            if updated_count >= max_capacity:
                print(f"⚠️ ДОСТИГНУТ ЛИМИТ СТРОКИ {OUTPUT_LIMIT_ROW} В {SHEET_OUTPUT}. Остальные пропущены.")
                break

            name = row[4]
            realm = row[3].lower()
            reg = row[1].lower()
            slug = row[5].lower() or name.lower()
            
            print(f"[{updated_count+1}] Парсим: {name}...")
            
            headers = {"Authorization": f"Bearer {token}"}
            params = {"namespace": f"profile-{reg}", "locale": "en_US"}
            
            eq_url = f"https://{reg}.api.blizzard.com/profile/wow/character/{realm}/{slug}/equipment"
            eq_resp = requests.get(eq_url, params=params, headers=headers, timeout=15)
            
            prof_url = f"https://{reg}.api.blizzard.com/profile/wow/character/{realm}/{slug}"
            prof_resp = requests.get(prof_url, params=params, headers=headers, timeout=15)

            if eq_resp.status_code == 200:
                eq_data = eq_resp.json()
                out_row_data = [""] * 22 
                out_row_data[0] = name

                for item in eq_data.get("equipped_items", []):
                    slot = item.get("slot", {}).get("type")
                    lvl = item.get("level", {}).get("value")
                    if slot in SLOT_MAP:
                        out_row_data[SLOT_MAP[slot]] = lvl
                
                if prof_resp.status_code == 200:
                    prof_data = prof_resp.json()
                    out_row_data[AVG_ILVL_IDX] = prof_data.get("equipped_item_level", "")
                
                out_row_data[REALM_IDX] = row[3]
                out_row_data[REGION_IDX] = reg.upper()
                out_row_data[KEY_IDX] = f"{reg}|{realm}|{name.lower()}"
                out_row_data[LABEL_IDX] = f"{name} • {row[3]} ({reg.upper()})"
                
                results_to_write.append(out_row_data)
                updated_count += 1
            else:
                print(f"  ! Ошибка для {name}: {eq_resp.status_code}")

    if results_to_write:
        print(f"Записываю данные в лист {SHEET_OUTPUT} (Диапазон: от строки {OUTPUT_START_ROW})...")
        # 1. Очищаем целевой рабочий диапазон на листе UI_Ilvl
        clear_range = f"A{OUTPUT_START_ROW}:V{OUTPUT_LIMIT_ROW}"
        ws_out.batch_clear([clear_range])
        
        # 2. Пишем новые данные на лист UI_Ilvl
        update_range = f"A{OUTPUT_START_ROW}:V{OUTPUT_START_ROW + len(results_to_write) - 1}"
        ws_out.update(update_range, results_to_write)
        print(f"✅ УСПЕШНО. Обновлено чаров: {updated_count}. Данные сохранены в лист '{SHEET_OUTPUT}'.")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
    input("\nНажми Enter для выхода...")
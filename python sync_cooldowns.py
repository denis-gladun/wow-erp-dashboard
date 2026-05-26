import os
import sys
import traceback
import requests
import time
import gspread
from datetime import datetime, timezone, timedelta
from oauth2client.service_account import ServiceAccountCredentials

# =========================
# CONFIG
# =========================
CLIENT_ID = "ТУТ ЖИВЕТ ID"
CLIENT_SECRET = "ТУТ ЖИВЕТ SECRET"
SPREADSHEET_URL = "ТУТ ЖИВЕТ ССЫЛКА НА ТАБЛИЦУ"
SHEET_NAME = "Raid_CD"

RAID_NAME_MAP = {
    "the voidspire": "voidspire",
    "the dreamrift": "dreamrift",
    "march on quel'danas": "march",
}

RESET_RULES = {
    "us": {"weekday": 1, "hour": 15}, 
    "eu": {"weekday": 2, "hour": 4},  
}

def get_token(region="us"):
    url = f"https://{region}.battle.net/oauth/token"
    resp = requests.post(url, auth=(CLIENT_ID, CLIENT_SECRET), data={"grant_type": "client_credentials"}, timeout=15)
    return resp.json()["access_token"]

def get_reset_ms(region):
    rule = RESET_RULES.get(region.lower(), RESET_RULES["us"])
    now = datetime.now(timezone.utc)
    reset = now.replace(hour=rule["hour"], minute=0, second=0, microsecond=0)
    days_back = (now.weekday() - rule["weekday"]) % 7
    reset -= timedelta(days=days_back)
    if now < reset: reset -= timedelta(days=7)
    return int(reset.timestamp() * 1000)

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(script_dir, "key.json")

    try:
        print("--- КИРА 3.0: РЕЙДОВЫЙ КОМБАЙН (FIXED) ---")
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(key_path, scope)
        client = gspread.authorize(creds)
        ss = client.open_by_url(SPREADSHEET_URL)
        ws = ss.worksheet(SHEET_NAME)
        
        all_vals = ws.get_all_values()
        tokens = {"us": get_token("us"), "eu": get_token("eu")}
        
        updated = 0
        for i in range(4, len(all_vals)):
            row = all_vals[i]
            if len(row) < 20: continue 
            
            char_name = str(row[17]).strip() # Col R
            region = str(row[18]).strip().lower() # Col S
            realm = str(row[19]).strip() # Col T
            
            if not char_name or region not in tokens: continue
            
            print(f" -> {char_name} ({realm})...", end=" ")
            token = tokens[region]
            realm_slug = realm.lower().replace(" ", "-").replace("'", "")
            
            # API запрос с Bearer токеном в HEADERS (так надежнее)
            url = f"https://{region}.api.blizzard.com/profile/wow/character/{realm_slug}/{char_name.lower()}/encounters/raids"
            headers = {"Authorization": f"Bearer {token}"}
            params = {"namespace": f"profile-{region}", "locale": "en_US"}
            
            try:
                r = requests.get(url, headers=headers, params=params, timeout=15)
                
                if r.status_code != 200:
                    print(f"СКИП (Ошибка {r.status_code})")
                    continue

                data = r.json()
                reset_ms = get_reset_ms(region)
                kills = { ("voidspire", "N"): 0, ("voidspire", "H"): 0, ("voidspire", "M"): 0,
                          ("march", "N"): 0, ("march", "H"): 0, ("march", "M"): 0,
                          ("dreamrift", "N"): 0, ("dreamrift", "H"): 0, ("dreamrift", "M"): 0 }

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

                upd = [
                    kills[("voidspire", "N")], kills[("voidspire", "H")], kills[("voidspire", "M")], 
                    kills[("march", "N")], kills[("march", "H")], kills[("march", "M")],         
                    kills[("dreamrift", "N")], kills[("dreamrift", "H")], kills[("dreamrift", "M")], 
                    datetime.now().strftime("%d.%m %H:%M"), 
                    f"{char_name} • {realm} ({region.upper()})" # AE (Char_Label)
                ]
                
                ws.update(f"U{i+1}:AE{i+1}", [upd])
                updated += 1
                print("OK")
                time.sleep(0.4)

            except Exception as e:
                print(f"ОШИБКА: {e}")

        print(f"\nЗавершено. Обновлено чаров: {updated}")

    except Exception:
        traceback.print_exc()
    input("\nНажми ENTER...")

if __name__ == "__main__":
    main()
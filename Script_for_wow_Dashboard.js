// ==========================================
// 1. СУЩЕСТВУЮЩИЙ КОД (ОБНОВЛЕНИЕ ДОСТУПОВ)
// ==========================================
function updateDashboardProtections() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const HELPER_NAME = "Booster_Data_Helper"; 
  const DASHBOARD_NAME = "Dashboard"; 
  const helperSheet = ss.getSheetByName(HELPER_NAME); 
  const dashboardSheet = ss.getSheetByName(DASHBOARD_NAME); 

  if (!helperSheet || !dashboardSheet) return;

  const oldProtections = dashboardSheet.getProtections(SpreadsheetApp.ProtectionType.RANGE);
  oldProtections.forEach(p => p.remove());

  const data = helperSheet.getRange(5, 136, 26, 3).getValues();
  data.forEach((row, index) => {
    const boosterName = row[0]; 
    const email = row[1] ? row[1].toString().trim() : ""; 
    const rangesString = row[2] ? row[2].toString().trim() : ""; 

    if (email !== "" && rangesString !== "") {
      rangesString.split(",").forEach(rangeA1 => {
        try {
          const range = dashboardSheet.getRange(rangeA1.trim());
          const protection = range.protect().setDescription('Доступ для ' + boosterName);
          protection.removeEditors(protection.getEditors());
          protection.addEditor(Session.getEffectiveUser().getEmail());
          protection.addEditor(email);
          if (protection.canDomainEdit()) protection.setDomainEdit(false);
        } catch (e) {}
      });
    }
  });
  SpreadsheetApp.getUi().alert('Готово!');
}

// ==========================================
// 2. МЕНЮ И ИНИЦИАЛИЗАЦИЯ
// ==========================================
function onOpen() {
  SpreadsheetApp.getUi().createMenu('🛡️ Админ-панель') 
      .addItem('🔄 Обновить доступы бустеров', 'updateDashboardProtections') 
      .addItem('💾 Инициализировать базу дашборда', 'initDynamicDashboard')
      .addSeparator()
      .addItem('🗑️ Очистить базу (Новая неделя)', 'resetWeekly')
      .addToUi();
}

const DB_SHEET_NAME = "DB_RL_Dashboard";
const MAIN_AREA = "B4:N37"; // Единая область для скорости

function initDynamicDashboard() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let db = ss.getSheetByName(DB_SHEET_NAME) || ss.insertSheet(DB_SHEET_NAME).hideSheet();
  if (db.getLastRow() === 0) db.appendRow(["Raid", "Komplekt", "Data"]);

  const sheet = ss.getSheetByName("RL_Dashboard");
  const raid = String(sheet.getRange("A4").getValue());
  const komplekt = String(sheet.getRange("S4").getValue());

  PropertiesService.getDocumentProperties().setProperties({
    "LAST_RAID": raid,
    "LAST_KOMPLEKT": komplekt
  });

  saveState(raid, komplekt, sheet);
  SpreadsheetApp.getUi().alert("✅ Готово! База синхронизирована.");
}

// ==========================================
// 3. ТУРБО-ЛОГИКА (СОХРАНЕНИЕ И ЗАГРУЗКА)
// ==========================================
function onEdit(e) {
  if (!e || !e.range) return;
  const sheet = e.range.getSheet();
  if (sheet.getName() !== "RL_Dashboard") return;

  const row = e.range.getRow();
  const col = e.range.getColumn();
  const isRaid = (row >= 4 && row <= 5 && col >= 1 && col <= 4);
  const isKomplekt = (row >= 4 && row <= 5 && col >= 19 && col <= 20);

  if (isRaid || isKomplekt) {
    const props = PropertiesService.getDocumentProperties();
    const currentRaid = String(sheet.getRange("A4").getValue());
    const currentKomplekt = String(sheet.getRange("S4").getValue());
    const oldRaid = props.getProperty("LAST_RAID") || currentRaid;
    const oldKomplekt = props.getProperty("LAST_KOMPLEKT") || currentKomplekt;

    if (oldRaid === currentRaid && oldKomplekt === currentKomplekt) return;

    saveState(oldRaid, oldKomplekt, sheet);
    loadState(currentRaid, currentKomplekt, sheet);

    props.setProperties({"LAST_RAID": currentRaid, "LAST_KOMPLEKT": currentKomplekt});
  }
}

function saveState(raid, komplekt, sheet) {
  const db = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(DB_SHEET_NAME);
  const range = sheet.getRange(MAIN_AREA);
  const data = { v: range.getValues(), f: range.getFormulas() };
  
  const rStr = String(raid);
  const kStr = String(komplekt);
  const json = JSON.stringify(data);

  let dbData = db.getDataRange().getValues();
  let foundRow = -1;
  for (let i = 1; i < dbData.length; i++) {
    if (String(dbData[i][0]) === rStr && String(dbData[i][1]) === kStr) {
      foundRow = i + 1;
      break;
    }
  }

  if (foundRow > 0) db.getRange(foundRow, 3).setValue(json);
  else db.appendRow([rStr, kStr, json]);
}

function loadState(raid, komplekt, sheet) {
  const db = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(DB_SHEET_NAME);
  const dbData = db.getDataRange().getValues();
  const rStr = String(raid);
  const kStr = String(komplekt);
  
  let json = null;
  for (let i = 1; i < dbData.length; i++) {
    if (String(dbData[i][0]) === rStr && String(dbData[i][1]) === kStr) {
      json = dbData[i][2];
      break;
    }
  }

  const range = sheet.getRange(MAIN_AREA);
  const currentFormulas = range.getFormulas();
  const currentValues = range.getValues();
  const parsed = json ? JSON.parse(json) : null;
  const finalData = [];

  for (let r = 0; r < currentValues.length; r++) {
    let rowValues = [];
    for (let c = 0; c < currentValues[r].length; c++) {
      const absRow = r + 4; // Смещение т.к. начинаем с 4 строки
      const absCol = c + 2; // Смещение т.к. начинаем со столбца B

      // БЕЛЫЙ СПИСОК (только эти зоны скрипт имеет право менять)
      let isTarget = false;
      if (absRow === 4 && absCol >= 5 && absCol <= 14) isTarget = true; // E4:N4
      else if (absRow === 5 && absCol >= 5 && absCol <= 13) isTarget = true; // E5:M5
      else if (absRow >= 7 && absRow <= 31 && absCol >= 5 && absCol <= 14) isTarget = true; // E7:N31
      else if (absRow >= 33 && absRow <= 37 && absCol >= 5 && absCol <= 14) isTarget = true; // E33:N37
      else if (absRow >= 33 && absRow <= 37 && absCol >= 2 && absCol <= 4) isTarget = true; // B33:D37

      if (!isTarget) {
        // ЗАЩИЩЕННАЯ ЗОНА (B7:D31, E6:M6 и всё остальное) - оставляем как есть!
        rowValues.push(currentFormulas[r][c] || currentValues[r][c]);
      } else {
        // ЗОНА ВВОДА (разрешено менять)
        if (currentFormulas[r][c] !== "") {
          // Если в целевой зоне есть формула (например E33:M37), сохраняем её!
          rowValues.push(currentFormulas[r][c]);
        } else if (parsed) {
          // Загружаем из базы
          rowValues.push(parsed.f[r][c] !== "" ? parsed.f[r][c] : parsed.v[r][c]);
        } else {
          // ДЕФОЛТ (база пустая)
          if (absRow >= 7 && absRow <= 31 && absCol >= 5 && absCol <= 13) {
            rowValues.push(1); // Ставим 1 для новых комплектов
          } else {
            rowValues.push(""); // Остальное пустое
          }
        }
      }
    }
    finalData.push(rowValues);
  }
  range.setValues(finalData);
}

function resetWeekly() {
  const ui = SpreadsheetApp.getUi();
  if (ui.alert("⚠️ Сброс недели?", "Это удалит ВСЕ сохраненные данные!", ui.ButtonSet.YES_NO) !== ui.Button.YES) return;
  
  const db = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(DB_SHEET_NAME);
  if (db && db.getLastRow() > 1) db.deleteRows(2, db.getLastRow() - 1);
  
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName("RL_Dashboard");
  loadState("VOID", "NULL", sheet); // Принудительно грузим дефолт
  saveState(sheet.getRange("A4").getValue(), sheet.getRange("S4").getValue(), sheet);
  ui.alert("✅ База очищена!");
}
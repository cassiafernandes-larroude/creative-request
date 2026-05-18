/**
 * Creative Request endpoint + Meta Ad pause proxy (token hardcoded).
 *
 * Setup (one-time):
 *  1. Open script.google.com → this script
 *  2. Replace the entire file with this code (already has the token below)
 *  3. Deploy → Manage deployments → Edit → New version → Deploy
 *  4. URL stays the same: AKfycbxVWmk3Ee1...
 */

const SHEET_ID = "1GLgNjLzCqNdRyhB5V8lCh5LDT2VSiVnyXUJZQ0zpUss";
const TAB_NAME = "US Request - Meta";
const META_API_VERSION = "v21.0";
const META_ACCESS_TOKEN = "EAAU67lUVGRMBRE4KDnpgG6gxkQxPYHzXp8PUZAelEhRwnvhyNOUnyQJZADA43EUS2BvnfBaZCpAaqCP0A7aqKXlxO5RyxZASdnxkSwvJiPhtDKIm9ok3C6ZBpJn2ih73uV8kVZCZCkQIz8HZAGPyzt7rbZAEAIGPDZA8ZBKaZCUA7xOj9ZCfIOWwV3gdvxyDYzBiJPk45eQZDZD";

function doPost(e) {
  try {
    Logger.log("doPost invoked. e.parameter keys: " + (e && e.parameter ? Object.keys(e.parameter).join(",") : "none"));
    Logger.log("e.postData type: " + (e && e.postData ? e.postData.type : "none"));
    Logger.log("e.postData.contents (first 500): " + (e && e.postData && e.postData.contents ? e.postData.contents.slice(0,500) : "none"));

    // Support both: raw JSON body AND form-urlencoded with `payload` field (used by dashboard)
    let body;
    if (e.parameter && e.parameter.payload) {
      body = JSON.parse(e.parameter.payload);
    } else if (e.postData && e.postData.contents) {
      try {
        body = JSON.parse(e.postData.contents);
      } catch (err) {
        const m = e.postData.contents.match(/^payload=(.*)/);
        if (m) body = JSON.parse(decodeURIComponent(m[1]));
        else throw err;
      }
    } else {
      throw new Error("No request body");
    }

    Logger.log("body parsed. keys: " + Object.keys(body).join(","));
    Logger.log("body.rows length: " + (body.rows ? body.rows.length : "no rows key"));

    // Action: pause a Meta ad
    if (body.action === "pause_ad") {
      return jsonOut(pauseMetaAd_(body.ad_id));
    }

    // Default: append rows to spreadsheet
    const rows = body.rows;
    if (!Array.isArray(rows) || rows.length === 0) {
      throw new Error("No rows to insert");
    }
    const ss = SpreadsheetApp.openById(SHEET_ID);
    const sheet = ss.getSheetByName(TAB_NAME);
    if (!sheet) throw new Error("Tab '" + TAB_NAME + "' not found");
    const startRow = sheet.getLastRow() + 1;
    // Strategy: clear data validations on the new row temporarily, do bulk write, restore validations
    const numCols = rows[0].length;
    const targetRange = sheet.getRange(startRow, 1, rows.length, numCols);
    // Save the validations from the row above (template), then clear current row, write, restore
    const templateRow = startRow > 2 ? sheet.getRange(startRow - 1, 1, 1, numCols) : null;
    const savedValidations = templateRow ? templateRow.getDataValidations() : null;
    targetRange.clearDataValidations();
    const failedCells = [];
    try {
      targetRange.setValues(rows);
      Logger.log("Bulk setValues OK");
    } catch (bulkErr) {
      Logger.log("Bulk failed: " + bulkErr.message + ". Falling back cell-by-cell.");
      // Per-cell fallback (validations are already cleared so this should pass)
      for (let r = 0; r < rows.length; r++) {
        const row = rows[r];
        const targetRow = startRow + r;
        for (let c = 0; c < row.length; c++) {
          const cell = sheet.getRange(targetRow, c + 1);
          try { cell.setValue(row[c]); }
          catch (cellErr) {
            try { cell.setValue(""); } catch (e2) {}
            failedCells.push("R" + targetRow + "C" + (c + 1) + ": " + cellErr.message.slice(0, 80));
          }
        }
      }
    }
    SpreadsheetApp.flush();
    // Restore data validations from template row to new rows
    if (savedValidations && savedValidations.length > 0) {
      for (let r = 0; r < rows.length; r++) {
        try {
          sheet.getRange(startRow + r, 1, 1, numCols).setDataValidations(savedValidations);
        } catch (vErr) {
          Logger.log("Failed to restore validations row " + (startRow + r) + ": " + vErr.message);
        }
      }
    }
    Logger.log("Inserted rows. Failed cells: " + failedCells.length);
    if (failedCells.length) Logger.log("Failures: " + failedCells.join(" | "));
    return jsonOut({
      ok: true,
      rowsAdded: rows.length,
      startRow: startRow,
      failedCells: failedCells,
      sheetUrl: ss.getUrl() + "#gid=" + sheet.getSheetId() + "&range=A" + startRow
    });
  } catch (err) {
    return jsonOut({ ok: false, error: err.toString() });
  }
}

function doGet() {
  return jsonOut({
    ok: true,
    message: "Endpoint live. POST {rows:[[...]]} or {action:'pause_ad', ad_id:'...'}"
  });
}

function pauseMetaAd_(adId) {
  if (!adId) return { ok: false, error: "ad_id required" };
  const url = "https://graph.facebook.com/" + META_API_VERSION + "/" + adId;
  const options = {
    method: "post",
    payload: { status: "PAUSED", access_token: META_ACCESS_TOKEN },
    muteHttpExceptions: true
  };
  const resp = UrlFetchApp.fetch(url, options);
  const code = resp.getResponseCode();
  let parsed = null;
  try { parsed = JSON.parse(resp.getContentText()); } catch (e) {}

  if (code >= 200 && code < 300) {
    return { ok: true, ad_id: adId, status: "PAUSED", api_response: parsed };
  }
  return {
    ok: false,
    ad_id: adId,
    http_code: code,
    error: (parsed && parsed.error && parsed.error.message) || resp.getContentText()
  };
}

function jsonOut(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

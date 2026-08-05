/**
 * Matchmaker → Google Sheet — the receiving end of the one-way new-incorps feed.
 *
 *   CHStream → Matchmaker API (/new-incorps/ingest) → THIS SCRIPT → the Sheet
 *
 * ONE-WAY BY DESIGN. This script only ever WRITES. There is deliberately no
 * doGet, so the published URL cannot be used to read the sheet, and Matchmaker
 * holds no Google credentials — nothing an AE types here can travel back.
 *
 * It writes only the columns whose header matches a key Matchmaker sent, so any
 * extra columns you add (Claimed by, Status, Notes...) are never overwritten.
 * The header row is the source of truth: reorder, rename-to-drop or delete
 * columns freely and the script follows the sheet, not the other way round.
 *
 * SETUP: see README.md next to this file. In short — paste this into
 * Extensions → Apps Script, set SHARED_KEY, Deploy → Web app (Execute as: me,
 * Access: Anyone), then give the API that /exec URL + the same key.
 */

// MUST match the API service's SHEET_WEBHOOK_KEY env var exactly.
// A published web app is a public URL — this key is the only thing stopping
// anyone who guesses it from writing rows into your sheet.
var SHARED_KEY = 'PASTE_THE_SAME_LONG_RANDOM_STRING_HERE';

// Columns the AEs own. Added when a tab is first created; never written again.
var AE_COLUMNS = ['Claimed by', 'Status', 'Notes'];

// How many rows from the top to check for duplicates. New rows land on top, so
// the recent past is all that matters. Bigger = safer, slower — and since
// high-value rows now arrive one at a time in real time, per-call cost is paid
// far more often, so this is deliberately modest. The API de-duplicates too
// (10,000 keys in memory); this only has to catch what an API restart forgets.
var DEDUPE_ROWS = 750;

var KEY_COLUMN = 'Company number';   // what "the same company" means

// Columns that must stay TEXT. Sheets would helpfully turn company number
// '01234567' into the number 1234567 — which breaks the Companies House link,
// breaks duplicate detection, and is just wrong. Same for a '03/1985' DOB, which
// it would read as a date.
var TEXT_COLUMNS = ['Company number', 'Director DOB', 'Postcode'];


function doPost(e) {
  var lock = LockService.getScriptLock();
  // Batches can overlap when the feed is busy; serialise them or two inserts
  // race and one silently overwrites the other.
  try {
    lock.waitLock(30000);
  } catch (err) {
    return reply({ ok: false, error: 'busy' });
  }
  try {
    var body = JSON.parse(e.postData.contents);
    if (!body.key || body.key !== SHARED_KEY) {
      return reply({ ok: false, error: 'bad key' });
    }
    var written = {};
    var sheets = body.sheets || {};
    Object.keys(sheets).forEach(function (tabName) {
      written[tabName] = writeRows(tabName, sheets[tabName] || [], body.columns || []);
    });
    return reply({ ok: true, written: written });
  } catch (err) {
    return reply({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}


/** Append `rows` (array of {header: value}) to `tabName`, newest at the top. */
function writeRows(tabName, rows, defaultColumns) {
  if (!rows.length) return 0;
  var sheet = getOrCreateTab(tabName, defaultColumns);
  var headers = addMissingColumns(sheet, defaultColumns);

  var fresh = rows.filter(notAlreadyPresent(sheet, headers));
  if (!fresh.length) return 0;

  // Newest on top: insert a block under the header, and reverse the batch so
  // the last company to arrive ends up in row 2.
  var matrix = fresh.reverse().map(function (row) {
    return headers.map(function (header) {
      return Object.prototype.hasOwnProperty.call(row, header) ? row[header] : '';
    });
  });
  sheet.insertRowsAfter(1, matrix.length);
  // Force the text columns BEFORE writing, or Sheets silently reinterprets them.
  TEXT_COLUMNS.forEach(function (name) {
    var i = headers.indexOf(name);
    if (i !== -1) sheet.getRange(2, i + 1, matrix.length, 1).setNumberFormat('@');
  });
  sheet.getRange(2, 1, matrix.length, headers.length).setValues(matrix);
  return matrix.length;
}


/**
 * Returns the tab's header row, first adding any column Matchmaker now sends
 * that the sheet doesn't have yet — so a new field (e.g. 'First director', added
 * 2026-08-05) appears on EXISTING tabs instead of being silently dropped.
 *
 * New columns are inserted before the AE-owned ones, so 'Claimed by / Status /
 * Notes' stay on the right where people expect them. A column you deliberately
 * DELETED comes back the next time it's sent — if you don't want a field, hide
 * the column rather than deleting it.
 */
function addMissingColumns(sheet, wanted) {
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var missing = (wanted || []).filter(function (name) {
    return name !== '' && headers.indexOf(name) === -1;
  });
  if (!missing.length) return headers;

  // Insert before the first AE column if there is one, else append at the end.
  var insertAt = headers.length;
  for (var i = 0; i < headers.length; i++) {
    if (AE_COLUMNS.indexOf(headers[i]) !== -1) { insertAt = i; break; }
  }
  sheet.insertColumnsAfter(insertAt === 0 ? 1 : insertAt, missing.length);
  if (insertAt === 0) insertAt = 1;          // can't insert before column A
  sheet.getRange(1, insertAt + 1, 1, missing.length).setValues([missing])
    .setFontWeight('bold').setBackground('#f1f3f4');
  return sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
}


/** A filter that drops companies already sitting near the top of the sheet. */
function notAlreadyPresent(sheet, headers) {
  var keyIndex = headers.indexOf(KEY_COLUMN);
  if (keyIndex === -1) return function () { return true; };   // no key column: keep everything

  var lastRow = sheet.getLastRow();
  var seen = {};
  if (lastRow > 1) {
    var count = Math.min(DEDUPE_ROWS, lastRow - 1);
    sheet.getRange(2, keyIndex + 1, count, 1).getValues().forEach(function (r) {
      if (r[0] !== '') seen[normaliseKey(r[0])] = true;
    });
  }
  return function (row) {
    var key = normaliseKey(row[KEY_COLUMN]);
    if (!key || seen[key]) return false;
    seen[key] = true;        // also de-dupes within this batch
    return true;
  };
}


/** Company numbers compare leniently: a row written before the text-format fix
 *  (or pasted by hand) can be sitting there as the number 1234567 where we send
 *  '01234567'. Strip leading zeros on both sides so they still match. */
function normaliseKey(value) {
  return String(value == null ? '' : value).trim().toUpperCase().replace(/^0+/, '');
}


/** The tab, created with headers + sensible formatting if it doesn't exist. */
function getOrCreateTab(tabName, defaultColumns) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(tabName);
  if (!sheet) {
    sheet = ss.insertSheet(tabName);
  }
  if (sheet.getLastRow() === 0 || sheet.getRange(1, 1).getValue() === '') {
    var headers = defaultColumns.concat(AE_COLUMNS);
    sheet.getRange(1, 1, 1, headers.length).setValues([headers])
      .setFontWeight('bold').setBackground('#f1f3f4');
    sheet.setFrozenRows(1);
    var capital = headers.indexOf('Starting capital');
    if (capital !== -1) {
      sheet.getRange(2, capital + 1, sheet.getMaxRows() - 1, 1).setNumberFormat('£#,##0');
    }
    sheet.setColumnWidth(headers.indexOf('Company') + 1, 260);
  }
  return sheet;
}


function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}


/**
 * Run this once from the Apps Script editor (Run ▸ testWrite) to check the
 * script works before pointing the API at it. It writes one obviously fake row
 * to each tab — delete the rows afterwards.
 */
function testWrite() {
  var res = doPost({ postData: { contents: JSON.stringify({
    key: SHARED_KEY,
    columns: ['Received', 'Company', 'Company number', 'High value', 'Why high value'],
    sheets: {
      'New Incorps': [{ Received: '2026-01-01 00:00:00', Company: 'TEST ROW LTD',
                        'Company number': 'TEST0001' }],
      'High Value':  [{ Received: '2026-01-01 00:00:00', Company: 'TEST ROW LTD',
                        'Company number': 'TEST0001', 'High value': 'Yes',
                        'Why high value': 'Capital £50,000' }]
    }
  }) } });
  Logger.log(res.getContent());
}

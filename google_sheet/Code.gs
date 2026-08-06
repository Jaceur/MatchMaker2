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
    var opts = {
      columns: body.columns || [],
      aeColumns: body.aeColumns || AE_COLUMNS,
      keyColumn: body.keyColumn || KEY_COLUMN,
      validation: body.validation || null,
      presence: body.presence || null
    };
    Object.keys(sheets).forEach(function (tabName) {
      written[tabName] = body.mode === 'sync'
        ? syncRows(tabName, sheets[tabName] || [], opts)
        : writeRows(tabName, sheets[tabName] || [], opts);
    });
    return reply({ ok: true, written: written });
  } catch (err) {
    return reply({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}


/** Append `rows` (array of {header: value}) to `tabName`, newest at the top. */
function writeRows(tabName, rows, opts) {
  if (!rows.length) return 0;
  var sheet = getOrCreateTab(tabName, opts);
  var headers = addMissingColumns(sheet, opts.columns, opts.aeColumns);

  var fresh = rows.filter(notAlreadyPresent(sheet, headers, opts.keyColumn));
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
 * SYNC MODE — for a tab that mirrors a LIST that changes (the pipeline), rather
 * than a stream of new events.
 *
 * Rows are matched on `opts.keyColumn`:
 *   - already in the sheet  -> its Matchmaker columns are refreshed in place
 *   - new                   -> inserted at the top
 *   - in the sheet but NO LONGER SENT -> marked in the presence column
 *     ('In pipeline' -> 'Left'), never deleted
 *
 * Deleting departed rows would take your Outcome and Notes with them, which is
 * the opposite of useful — you want to see what you did with a lead after it
 * left the pipeline.
 *
 * **Your columns are never written.** Only headers Matchmaker actually sent are
 * updated, one whole column at a time, so anything you typed (or any formula you
 * wrote) in Outcome/Notes is not read, not rewritten, and not disturbed.
 */
function syncRows(tabName, rows, opts) {
  var sheet = getOrCreateTab(tabName, opts);
  var headers = addMissingColumns(sheet, opts.columns, opts.aeColumns);
  applyValidation(sheet, headers, opts.validation);

  var keyIndex = headers.indexOf(opts.keyColumn);
  if (keyIndex === -1) return 0;          // no key column: nothing safe to do

  var incoming = {};                       // key -> row object
  rows.forEach(function (row) {
    var k = normaliseKey(row[opts.keyColumn]);
    if (k && !incoming[k]) incoming[k] = row;
  });

  // Which keys are already in the sheet?
  var lastRow = sheet.getLastRow();
  var present = {};
  if (lastRow > 1) {
    sheet.getRange(2, keyIndex + 1, lastRow - 1, 1).getValues().forEach(function (r) {
      var k = normaliseKey(r[0]);
      if (k) present[k] = true;
    });
  }

  // 1. Insert brand-new rows at the top (newest first, same as the feed).
  var fresh = Object.keys(incoming)
    .filter(function (k) { return !present[k]; })
    .map(function (k) { return incoming[k]; });
  if (fresh.length) {
    var matrix = fresh.reverse().map(function (row) {
      return headers.map(function (h) {
        return Object.prototype.hasOwnProperty.call(row, h) ? row[h] : '';
      });
    });
    sheet.insertRowsAfter(1, matrix.length);
    sheet.getRange(2, 1, matrix.length, headers.length).setValues(matrix);
  }

  // 2. Refresh the Matchmaker columns of every data row, one column at a time so
  //    the AE-owned columns are never even touched.
  lastRow = sheet.getLastRow();
  if (lastRow < 2) return fresh.length;
  var height = lastRow - 1;
  var keys = sheet.getRange(2, keyIndex + 1, height, 1).getValues();
  var presenceCol = opts.presence ? opts.presence.column : null;

  headers.forEach(function (header, col) {
    if (!header || opts.columns.indexOf(header) === -1) return;   // not ours
    var existing = sheet.getRange(2, col + 1, height, 1).getValues();
    var out = [];
    var changed = false;
    for (var i = 0; i < height; i++) {
      var row = incoming[normaliseKey(keys[i][0])];
      var value;
      if (row) {
        value = Object.prototype.hasOwnProperty.call(row, header) ? row[header] : existing[i][0];
      } else if (header === presenceCol && existing[i][0] !== '') {
        value = opts.presence.absent;      // it left the pipeline
      } else {
        value = existing[i][0];            // an older row we're not sent any more
      }
      if (value !== existing[i][0]) changed = true;
      out.push([value]);
    }
    if (changed) sheet.getRange(2, col + 1, height, 1).setValues(out);
  });
  return Object.keys(incoming).length;
}


/** Apply dropdowns: {'Outcome': ['Net New', ...]} -> data validation on that column. */
function applyValidation(sheet, headers, validation) {
  if (!validation) return;
  Object.keys(validation).forEach(function (name) {
    var col = headers.indexOf(name);
    if (col === -1) return;
    var rule = SpreadsheetApp.newDataValidation()
      .requireValueInList(validation[name], true)
      .setAllowInvalid(true)          // never block a value someone typed
      .build();
    sheet.getRange(2, col + 1, Math.max(sheet.getMaxRows() - 1, 1), 1).setDataValidation(rule);
  });
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
function addMissingColumns(sheet, wanted, aeColumns) {
  aeColumns = aeColumns || AE_COLUMNS;
  var headers = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var missing = (wanted || []).filter(function (name) {
    return name !== '' && headers.indexOf(name) === -1;
  });
  if (!missing.length) return headers;

  // Insert before the first AE column if there is one, else append at the end.
  var insertAt = headers.length;
  for (var i = 0; i < headers.length; i++) {
    if (aeColumns.indexOf(headers[i]) !== -1) { insertAt = i; break; }
  }
  sheet.insertColumnsAfter(insertAt === 0 ? 1 : insertAt, missing.length);
  if (insertAt === 0) insertAt = 1;          // can't insert before column A
  sheet.getRange(1, insertAt + 1, 1, missing.length).setValues([missing])
    .setFontWeight('bold').setBackground('#f1f3f4');
  return sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
}


/** A filter that drops companies already sitting near the top of the sheet. */
function notAlreadyPresent(sheet, headers, keyColumn) {
  var keyIndex = headers.indexOf(keyColumn || KEY_COLUMN);
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
    var key = normaliseKey(row[keyColumn || KEY_COLUMN]);
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
function getOrCreateTab(tabName, opts) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(tabName);
  if (!sheet) {
    sheet = ss.insertSheet(tabName);
  }
  if (sheet.getLastRow() === 0 || sheet.getRange(1, 1).getValue() === '') {
    var headers = opts.columns.concat(opts.aeColumns || AE_COLUMNS);
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
    aeColumns: AE_COLUMNS,
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

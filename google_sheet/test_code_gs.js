/* Tests Code.gs against an in-memory fake of the Google Sheets API, so the
   script can be changed without pasting it into a live spreadsheet to find out.
   The one it really guards: an AE's own columns must survive a row insert.

       node google_sheet/test_code_gs.js        (from the project root)
*/
const fs = require('fs');
const path = require('path');

// ---- fake spreadsheet -----------------------------------------------------
function makeSheet(name) {
  const grid = [];                       // grid[r][c], 0-indexed
  const get = (r, c) => (grid[r] && grid[r][c] !== undefined ? grid[r][c] : '');
  const sheet = {
    name,
    grid,
    getLastRow: () => grid.length,
    getLastColumn: () => grid.reduce((m, row) => Math.max(m, row.length), 0),
    getMaxRows: () => Math.max(grid.length, 1000),
    setFrozenRows: () => sheet,
    setColumnWidth: () => sheet,
    insertRowsAfter: (after, howMany) => {
      const blank = Array.from({ length: howMany }, () => []);
      grid.splice(after, 0, ...blank);
      return sheet;
    },
    getRange: (row, col, numRows = 1, numCols = 1) => ({
      getValues: () => {
        const out = [];
        for (let r = 0; r < numRows; r++) {
          const line = [];
          for (let c = 0; c < numCols; c++) line.push(get(row - 1 + r, col - 1 + c));
          out.push(line);
        }
        return out;
      },
      getValue: () => get(row - 1, col - 1),
      setValues: (values) => {
        values.forEach((line, r) => {
          const target = row - 1 + r;
          if (!grid[target]) grid[target] = [];
          line.forEach((v, c) => { grid[target][col - 1 + c] = v; });
        });
        return { setFontWeight: () => ({ setBackground: () => {} }) };
      },
      setNumberFormat: () => {},
    }),
  };
  return sheet;
}

const sheets = {};
global.SpreadsheetApp = {
  getActiveSpreadsheet: () => ({
    getSheetByName: (n) => sheets[n] || null,
    insertSheet: (n) => (sheets[n] = makeSheet(n)),
  }),
};
global.LockService = { getScriptLock: () => ({ waitLock: () => {}, releaseLock: () => {} }) };
global.ContentService = {
  MimeType: { JSON: 'json' },
  createTextOutput: (s) => ({ getContent: () => s, setMimeType: () => ({ getContent: () => s }) }),
};
global.Logger = { log: (...a) => console.log(...a) };

// ---- load the real script -------------------------------------------------
const code = fs.readFileSync(path.join(__dirname, 'Code.gs'), 'utf8');
eval(code);
SHARED_KEY = 'test-key';

const COLUMNS = ['Received', 'Company', 'Company number', 'Starting capital',
                 'High value', 'Why high value'];
const post = (body) => JSON.parse(
  doPost({ postData: { contents: JSON.stringify(body) } }).getContent());
const row = (crn, name, extra = {}) => Object.assign(
  { Received: '2026-08-04 09:00:00', Company: name, 'Company number': crn }, extra);

let failures = 0;
const check = (label, cond) => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}`);
  if (!cond) failures++;
};

// 1. bad key is rejected
check('bad key rejected', post({ key: 'wrong', sheets: {} }).ok === false);

// 2. first write creates tabs + headers (+ the AE columns)
post({ key: 'test-key', columns: COLUMNS,
       sheets: { 'New Incorps': [row('1', 'ALPHA LTD')],
                 'High Value': [row('1', 'ALPHA LTD', { 'High value': 'Yes' })] } });
const all = sheets['New Incorps'];
check('tabs created', !!all && !!sheets['High Value']);
check('header includes AE columns',
      all.grid[0].join('|') === COLUMNS.concat(['Claimed by', 'Status', 'Notes']).join('|'));
check('first row written under the header', all.grid[1][1] === 'ALPHA LTD');

// 3. an AE fills in their own columns...
all.grid[1][6] = 'josh';                         // "Claimed by"
all.grid[1][8] = 'called them';                  // "Notes"

// 4. ...a later batch lands ON TOP and must not disturb it
post({ key: 'test-key', columns: COLUMNS,
       sheets: { 'New Incorps': [row('2', 'BETA LTD'), row('3', 'GAMMA LTD')] } });
check('newest batch is on top, newest-of-batch first',
      all.grid[1][1] === 'GAMMA LTD' && all.grid[2][1] === 'BETA LTD');
check('AE data moved down with its own row, intact',
      all.grid[3][1] === 'ALPHA LTD' && all.grid[3][6] === 'josh' && all.grid[3][8] === 'called them');

// 5. duplicates are skipped
const before = all.grid.length;
post({ key: 'test-key', columns: COLUMNS, sheets: { 'New Incorps': [row('2', 'BETA LTD')] } });
check('duplicate company number skipped', all.grid.length === before);

// 6. duplicates WITHIN one batch are skipped too
post({ key: 'test-key', columns: COLUMNS,
       sheets: { 'New Incorps': [row('7', 'DELTA LTD'), row('7', 'DELTA LTD')] } });
check('duplicate within a batch skipped', all.grid.filter(r => r[2] === '7').length === 1);

// 7. the sheet's header row wins: user reorders + deletes a column
const hv = sheets['High Value'];
hv.grid.forEach(r => r.splice(0, 1));            // user deleted "Received"
post({ key: 'test-key', columns: COLUMNS,
       sheets: { 'High Value': [row('9', 'EPSILON LTD', { 'High value': 'Yes',
                                                          'Why high value': 'Zone 1 (EC1V)' })] } });
check('maps by header name after a column is deleted',
      hv.grid[1][0] === 'EPSILON LTD' && hv.grid[1][1] === '9' && hv.grid[1][4] === 'Zone 1 (EC1V)');

// 8. a key Matchmaker doesn't send leaves the cell blank, never overwrites
check('unsent columns left blank', hv.grid[1][5] === '' || hv.grid[1][5] === undefined);

// 9. a legacy row Sheets coerced to a number still counts as a duplicate
all.grid.splice(1, 0, ['x', 'ZETA LTD', 1234567, '', '', '', '', '', '']);
const beforeZeta = all.grid.length;
post({ key: 'test-key', columns: COLUMNS, sheets: { 'New Incorps': [row('01234567', 'ZETA LTD')] } });
check('numeric company number still de-dupes against 0-padded', all.grid.length === beforeZeta);

console.log(failures ? `\n${failures} FAILURE(S)` : '\nAll Apps Script checks passed.');
process.exit(failures ? 1 : 0);

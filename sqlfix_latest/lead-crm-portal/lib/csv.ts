// RFC 4180 quoting plus spreadsheet formula-injection defence: a cell that starts with = + - @ TAB or CR
// is prefixed with an apostrophe so Excel/Sheets show it as text instead of executing it.
export function csvCell(value: unknown): string {
  let s = value == null ? "" : typeof value === "object" ? JSON.stringify(value) : String(value);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

export function toCsv(rows: Record<string, unknown>[]): string {
  const headers = Object.keys(rows[0] || {});
  return [headers.map(csvCell).join(","), ...rows.map((r) => headers.map((h) => csvCell(r[h])).join(","))].join("\r\n");
}

export function downloadCsv(rows: Record<string, unknown>[], filename: string): void {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8" }));
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

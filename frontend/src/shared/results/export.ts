import type { Result } from "../api/contracts";

export function rawCell(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function csvCell(value: unknown) {
  let text = rawCell(value);
  if (/^[\s]*[=+@-]/.test(text) && !/^-?\d+(\.\d+)?$/.test(text))
    text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

export function resultCsv(result: Result) {
  return (
    "\uFEFF" +
    [
      result.columns.map((column) => csvCell(column.name)).join(";"),
      ...result.rows.map((row) => row.map(csvCell).join(";")),
    ].join("\r\n")
  );
}

export function downloadText(
  text: string,
  filename: string,
  type = "text/plain;charset=utf-8",
) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

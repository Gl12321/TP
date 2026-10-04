export function decimal(
  value: string | number | null | undefined,
  maxDecimals = 2,
): string {
  if (value === null || value === undefined || value === "") return "—";
  const raw = String(value);
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(raw);
  if (!match) return raw;
  const sourceFraction = match[3] ?? "";
  const places = Math.max(0, Math.min(sourceFraction.length, maxDecimals));
  let digits = BigInt(match[2] + sourceFraction.slice(0, places));
  if (sourceFraction.length > places && sourceFraction[places] >= "5")
    digits += 1n;
  const rounded = digits.toString().padStart(places + 1, "0");
  const integer = places ? rounded.slice(0, -places) : rounded;
  const fraction = places ? rounded.slice(-places).replace(/0+$/, "") : "";
  return `${match[1] === "-" && digits !== 0n ? "−" : ""}${integer.replace(/\B(?=(\d{3})+(?!\d))/g, "\u202f")}${fraction ? `,${fraction}` : ""}`;
}

export function compareDecimal(left: unknown, right: unknown): number {
  if (left === null || left === undefined)
    return right === null || right === undefined ? 0 : -1;
  if (right === null || right === undefined) return 1;
  const a = /^(-?)(\d+)(?:\.(\d+))?$/.exec(String(left));
  const b = /^(-?)(\d+)(?:\.(\d+))?$/.exec(String(right));
  if (!a || !b)
    return String(left).localeCompare(String(right), "ru", { numeric: true });
  const scale = Math.max(a[3]?.length ?? 0, b[3]?.length ?? 0);
  const av = BigInt(`${a[1]}${a[2]}${(a[3] ?? "").padEnd(scale, "0")}`);
  const bv = BigInt(`${b[1]}${b[2]}${(b[3] ?? "").padEnd(scale, "0")}`);
  return av < bv ? -1 : av > bv ? 1 : 0;
}

export function money(value: string | number | null | undefined, unit = "RUB") {
  const text = decimal(value);
  if (text === "—") return text;
  return `${text}${unit === "RUB" ? " ₽" : unit ? ` ${unit}` : ""}`;
}

export function percent(value: string | number | null | undefined) {
  return value === null || value === undefined ? "—" : `${decimal(value, 1)}%`;
}

export function dateTime(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("ru-RU", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      }).format(date);
}

export function periodLabel(from?: string | null, to?: string | null) {
  if (!from || !to) return "Период не задан";
  const render = (date: string) => date.split("-").reverse().join(".");
  return `${render(from)} — ${render(to)}`;
}

export function previousMonth() {
  const now = new Date();
  const month = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return `${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, "0")}`;
}

export function monthRange(month: string) {
  const [year, index] = month.split("-").map(Number);
  const end = new Date(year, index, 0).getDate();
  return { from: `${month}-01`, to: `${month}-${end}` };
}

export function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

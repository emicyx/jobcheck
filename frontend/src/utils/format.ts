export function parseServerDate(iso: string): Date {
  // 后端存储/序列化的是 UTC 且不带时区标记，补 Z 再解析，否则会被当成本地时间
  if (/T/.test(iso) && !/(Z|[+-]\d{2}:?\d{2})$/.test(iso)) return new Date(iso + "Z")
  return new Date(iso)
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = parseServerDate(iso)
  const p = (n: number) => String(n).padStart(2, "0")
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return ''
  return iso.slice(0, 10)
}

/**
 * The till's line calculator. It must match the server (common.line_amount) and Tryton:
 * quantity x unit price, rounded half-even ("banker's rounding") to whole shillings.
 * Discounted unit prices are rounded half-up first, as on the server.
 */
export function roundHalfEven(x: number): number {
  const scaled = Math.round(x * 1e6) / 1e6 // drop binary noise (0.1 * 3 = 0.30000000000000004)
  const floor = Math.floor(scaled)
  const diff = scaled - floor
  if (Math.abs(diff - 0.5) < 1e-9) return floor % 2 === 0 ? floor : floor + 1
  return Math.round(scaled)
}

export function unitAfterDiscount(price: number, discountPct: number): number {
  return discountPct ? Math.round(price * (100 - discountPct) / 100) : price
}

export function lineAmount(quantity: number, unitPrice: number): number {
  return roundHalfEven(quantity * unitPrice)
}

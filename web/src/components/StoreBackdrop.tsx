import {
  Apple, Banana, Carrot, Cherry, Citrus, Croissant, Egg, Fish, Grape, Milk, Package, ReceiptText, ScanBarcode,
  ShoppingBasket, ShoppingCart, Tag, Wheat, type LucideIcon,
} from 'lucide-react'

// Decorative supermarket motifs for the sign-in backdrop: baskets, carts, fresh produce, bakery, dairy,
// barcodes and receipts, drawn as light line art in the side margins (purely ornamental).
type Motif = { icon: LucideIcon; x: string; y: string; size: number; rotate: number; warm?: boolean; wide?: boolean }

const MOTIFS: Motif[] = [
  // left margin
  { icon: ShoppingBasket, x: '3%', y: '6%', size: 54, rotate: -10 },
  { icon: Apple, x: '6%', y: '19%', size: 30, rotate: 12, warm: true },
  { icon: Carrot, x: '3%', y: '31%', size: 36, rotate: -24, warm: true },
  { icon: Milk, x: '6%', y: '44%', size: 38, rotate: 6 },
  { icon: Croissant, x: '3%', y: '57%', size: 40, rotate: -8, warm: true },
  { icon: ScanBarcode, x: '6%', y: '70%', size: 34, rotate: 4 },
  { icon: Grape, x: '3%', y: '82%', size: 34, rotate: -12, warm: true },
  { icon: Tag, x: '6%', y: '93%', size: 28, rotate: 18 },
  // right margin
  { icon: ShoppingCart, x: '96.5%', y: '5%', size: 56, rotate: 8 },
  { icon: Banana, x: '94%', y: '17%', size: 36, rotate: -16, warm: true },
  { icon: ReceiptText, x: '97%', y: '29%', size: 36, rotate: 10 },
  { icon: Citrus, x: '94%', y: '41%', size: 32, rotate: -6, warm: true },
  { icon: Wheat, x: '97%', y: '53%', size: 40, rotate: 14, warm: true },
  { icon: Fish, x: '94%', y: '65%', size: 38, rotate: -10 },
  { icon: Egg, x: '97%', y: '77%', size: 30, rotate: 8, warm: true },
  { icon: Package, x: '94%', y: '88%', size: 36, rotate: -4 },
  { icon: Cherry, x: '97%', y: '95%', size: 28, rotate: 12, warm: true },
  // a few nearer the middle on wide screens
  { icon: Apple, x: '24%', y: '2.5%', size: 24, rotate: -14, warm: true, wide: true },
  { icon: ShoppingBasket, x: '72%', y: '2%', size: 26, rotate: 10, wide: true },
  { icon: Carrot, x: '78%', y: '97%', size: 26, rotate: 20, warm: true, wide: true },
  { icon: Milk, x: '20%', y: '97.5%', size: 24, rotate: -8, wide: true },
]

export function StoreBackdrop() {
  return (
    <div aria-hidden className="store-motifs pointer-events-none absolute inset-0 -z-10 overflow-hidden">
      {MOTIFS.map(({ icon: Icon, x, y, size, rotate, warm, wide }, i) => (
        <Icon key={i} strokeWidth={1.5}
          className={`absolute ${warm ? 'warm' : ''} ${wide ? 'hidden xl:block' : 'hidden md:block'}`}
          style={{ left: x, top: y, width: size, height: size, transform: `translate(-50%, -50%) rotate(${rotate}deg)` }} />
      ))}
    </div>
  )
}

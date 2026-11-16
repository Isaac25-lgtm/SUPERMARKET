import clsx from 'clsx'
import {
  Boxes, BriefcaseBusiness, Calculator, Crown, ScanBarcode, ServerCog, Store, Truck, UsersRound, type LucideIcon,
} from 'lucide-react'
import type { CSSProperties } from 'react'

// One icon and one colour per role, used wherever a role is shown (sign-in picker, My access,
// Roles & permissions, the account chip). Colours are the --color-role-* tokens in index.css.
const ROLE_ICONS: Record<string, LucideIcon> = {
  owner: Crown,
  general_manager: BriefcaseBusiness,
  accountant: Calculator,
  operations_manager: Truck,
  inventory_manager: Boxes,
  branch_manager: Store,
  cashier: ScanBarcode,
  sysadmin: ServerCog,
}

const SIZES = {
  xs: { box: 'size-[18px] rounded-[6px]', icon: 'size-[11px]' },
  sm: { box: 'size-7 rounded-lg', icon: 'size-[15px]' },
  md: { box: 'size-9 rounded-[10px]', icon: 'size-[18px]' },
  lg: { box: 'size-11 rounded-xl', icon: 'size-[22px]' },
}

export function RoleIcon({ role, size = 'md', className, icon }: {
  role: string; size?: keyof typeof SIZES; className?: string; icon?: LucideIcon
}) {
  const Icon = icon ?? ROLE_ICONS[role] ?? UsersRound
  const s = SIZES[size]
  const style = { '--role': `var(--color-role-${ROLE_ICONS[role] ? role.replace('_', '-') : 'staff'})` } as CSSProperties
  return (
    <span aria-hidden className={clsx('role-tile grid shrink-0 place-items-center', s.box, className)} style={style}>
      <Icon className={s.icon} strokeWidth={2} />
    </span>
  )
}

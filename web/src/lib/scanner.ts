// Barcode scanner helpers for the till.
//
// USB/Bluetooth scanners in "keyboard" (HID) mode type the code very fast and
// press Enter. The till listens for that pattern anywhere on the page, so a
// scan works even when the cursor is not in the search box, while normal
// (slower) human typing is ignored by the global listener.

/** Forms of a scanned code to match: UPC-A (12 digits) equals EAN-13 with a leading 0. */
export function barcodeCandidates(raw: string): string[] {
  const code = raw.replace(/\s+/g, '')
  const out = [code]
  if (/^\d{12}$/.test(code)) out.push(`0${code}`)
  if (/^0\d{12}$/.test(code)) out.push(code.slice(1))
  return out
}

/** GS1 mod-10 check digit (EAN-8, UPC-A, EAN-13, GTIN-14). */
export function gtinValid(code: string): boolean {
  if (!/^\d{8}$|^\d{12,14}$/.test(code)) return false
  const digits = code.split('').map(Number)
  const check = digits.pop()!
  const total = digits.reverse().reduce((s, d, i) => s + d * (i % 2 === 0 ? 3 : 1), 0)
  return (10 - (total % 10)) % 10 === check
}

let audio: AudioContext | null = null

/** Short confirmation tone: high for a good scan, low for a problem. Silent if audio is unavailable. */
export function beep(ok: boolean) {
  try {
    audio ??= new AudioContext()
    const osc = audio.createOscillator()
    const gain = audio.createGain()
    osc.frequency.value = ok ? 1046 : 220
    osc.type = ok ? 'sine' : 'square'
    gain.gain.value = 0.06
    osc.connect(gain).connect(audio.destination)
    osc.start()
    osc.stop(audio.currentTime + (ok ? 0.07 : 0.25))
  } catch {
    /* no audio device / autoplay blocked — scanning still works */
  }
}

/**
 * Calls `onScan(code)` when keystrokes arrive scanner-fast (< 50 ms apart) and end
 * with Enter, while focus is not in another text field. Returns a cleanup function.
 */
export function listenForScanner(onScan: (code: string) => void): () => void {
  let buffer = ''
  let last = 0
  const handler = (e: KeyboardEvent) => {
    const el = document.activeElement as HTMLElement | null
    const typing = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)
    if (typing || e.ctrlKey || e.altKey || e.metaKey) return
    const now = performance.now()
    if (now - last > 50) buffer = ''
    last = now
    if (e.key === 'Enter') {
      if (buffer.length >= 4) {
        e.preventDefault()
        onScan(buffer)
      }
      buffer = ''
      return
    }
    if (e.key.length === 1) buffer += e.key
  }
  window.addEventListener('keydown', handler)
  return () => window.removeEventListener('keydown', handler)
}

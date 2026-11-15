# Barcode scanners: setup and daily use

## How it works
Most USB and Bluetooth barcode scanners act as a **keyboard** ("USB HID" or "keyboard wedge" mode). When a scanner
reads a barcode, it types the digits very quickly and presses **Enter**. Windows recognises it automatically, so
**no driver and no setup inside Motion Retail are needed**.

The till:
- **Listens everywhere on the page.** A scan works even when the cursor isn't in the search box. The till tells a
  scanner apart from a person by how fast the characters arrive.
- **Beeps** for each scan: a short high tone when the item is added, a low tone when the code isn't recognised.
- **Treats 12-digit (UPC-A) and 13-digit (EAN-13) forms of the same code as identical.**
- **Reads scale labels** (codes starting with `2`) for foodstuff sold by weight; the weight or price is read from
  the label.

## One-time setup per scanner (about 2 minutes)
1. Plug the scanner into a USB port and wait about 10 seconds for Windows to report the device ready.
2. Open **Notepad** and scan any product. You should see the digits, then the cursor jumps to a new line.
   - **Digits but no new line:** the scanner's *suffix* isn't Enter. In the scanner's manual, scan the setup barcode
     called **"Add CR suffix"**, **"Enter"** or **"Carriage return"**.
   - **Nothing, or strange characters:** the scanner is probably in *serial/COM* mode (the mode that needs the
     downloaded driver). Scan the manual's **"USB HID"** / **"USB Keyboard"** setup barcode.
   - **Wrong characters** (e.g. `é` instead of `2`): set the scanner's keyboard language to **US / English** using
     the manual, or match it to Windows' keyboard layout.
   - **Bluetooth scanners:** pair them in Windows Bluetooth settings like a keyboard.
3. Avoid prefixes: if the scanner adds characters before the code, turn the prefix off in the manual.
4. Open the till, open your shift and scan an item. It should beep and appear in the basket.

## One-time setup per product: register its barcode
A barcode only scans after it has been stored against its product.
1. Go to **Stock & products**, open the product, and find the **Barcodes** panel. This needs the Inventory Manager
   role.
2. Click into **Add a barcode** and scan the item. The code fills in and is registered: it scans at every till
   immediately.
3. A product can have several codes, such as the single bottle and the crate. Scan each one.
4. Check digits are verified. A misread or mistyped code is refused, as is a code already registered on another
   product (the message names that product).
5. **Foodstuff sold by weight** (rice, sugar, tomatoes, meat…) has no package barcode. Use the scale's printed
   label, or type the product's 5-digit **PLU** at the till and enter the weight.

## At the till
- **Unknown barcode:** a low beep and a message tell the cashier to ask the Inventory Manager to register the code.
  The cashier can still find the item by typing part of its name.
- **Offline branch till:** the branch edge server receives the barcode list with each sync, so scanning keeps
  working during internet outages.

## Not yet tested on hardware
Physical scanners, receipt printers and cash drawers haven't been tested with this system yet. Test them during
the branch pilot.

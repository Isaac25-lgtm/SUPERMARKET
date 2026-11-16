MOTION BRANCH BOX - keeps the tills selling when the internet is off
=====================================================================

What it is
  One program on the branch's back-office PC. The checkout computers open the till from it
  over the shop network. With internet, every sale goes to head office within seconds.
  Without internet, sales are saved on the back-office PC and upload by themselves when
  the internet returns. Nothing is installed on the checkout computers except a shortcut.

Before you start
  - Head office: in Motion, sign in as the technical administrator, open System, and under
    "Branch devices" register a device for this branch. Copy the token (starts with edge_).
    It is shown only once.
  - The back-office PC stays on during trading hours (Settings > Power: Sleep = Never),
    ideally on a UPS. All PCs must be on the same shop router / network.

1) Back-office PC (once)
  - Unzip this folder anywhere (for example the Desktop).
  - Right-click Start > "Terminal (Admin)" or "Windows PowerShell (Admin)".
  - Go to the folder and run:
        powershell -ExecutionPolicy Bypass -File .\install-box.ps1
  - Enter the head-office address and the token. Note the PC name and the fingerprint shown.

2) Each checkout computer (once per till)
  - Copy setup-till.ps1 to the checkout computer and run it as Administrator:
        powershell -ExecutionPolicy Bypass -File .\setup-till.ps1
  - Enter the back-office PC name. Check the fingerprint matches, type Y.
  - A "Motion Till" shortcut appears on the desktop.
  - Make the USB receipt printer the default printer.
  - Open the till, sign in as the cashier, choose this till in "Register" and open the shift.
    The till is remembered on this computer.

Every day
  - Cashiers use "Motion Till" as normal. The pill at the top shows:
      Online                  sales go to head office straight away
      Offline - N saved...    keep selling; sales upload automatically later
      N need review           head office could not accept something; the branch manager checks
  - Discounts above the cashier limit and refunds are approved on the till by the branch
    manager typing their own username and password.
  - The till can run offline for up to 24 hours. After that it asks to reconnect.

Updating
  - Run install-box.ps1 from the new version's folder. Saved sales are kept.

Removing
  - uninstall-box.ps1 (refuses while sales are still waiting to upload).

Files
  Program:  C:\Program Files\MotionBranchBox
  Data:     C:\ProgramData\MotionBranchBox  (sales database, settings, log: box.log)

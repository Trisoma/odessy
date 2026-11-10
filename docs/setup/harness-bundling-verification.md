# Harness bundling verification — November 10

The installer stages the pinned 0.2.1rc2 Windows runtime, sidecars and licenses from the
shared artifact inventory, composes the packaged plugin without pnpm, and retains legacy
as default. Role-aware install, budgets, repair and uninstall own the new component.

The HTTP closure now includes FastAPI 0.156.6 and annotated-doc 0.0.4, regenerated through
prepare_wheels with published wheel hashes. A fresh isolated six-stage role-aware workflow
completed with Python 3.12.13 and Node 22.23.0. The private interpreter successfully imports
FastAPI. Fresh child gateways need installed application registration; assembly now writes
an owned .pth file into the private dependency environment, never inherited PYTHONPATH.

Focused source results: 64 passed; two UI tests could not import Tkinter in the verification
interpreter. The installed interpreter does include Tkinter. Native setup build/run and
final installed interaction smoke remain separate qualification gates. Do not treat CLI-help
or successful staging as evidence that all HTTP/voice/consent interactions passed.

No Linux/Jetson or microphone checks were performed. Preserve all user data and optional
component choices. No automatic updates or engine-default change are introduced.

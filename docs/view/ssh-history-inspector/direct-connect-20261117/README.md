# SSH connection and connected devices

Developer Preview · v0.5.0-alpha · November 17, 2026

Actual Flutter renders of production widgets with deterministic test APIs.
These are simulated states, not live Jetson or installed-app evidence.

1. [Start with an address](01-address-entry.png).
2. [Enter optional port and masked password](02-optional-port-password.png). The
   example port 2222 is illustrative; leave blank for normal configured/default SSH.
3. [Connected, read-only status](03-connected-read-only.png).
4. [Unknown host key refused](04-unknown-key-refused.png).
5. [Changed key refused](05-changed-key-refused.png).
6. [Narrow, dark connection page](06-narrow-dark.png).
7. [Connecting and Stop waiting](07-connecting.png).
8. [Devices overview with connected hostname/address](08-devices-overview.png).
9. [Connected device details](09-device-details.png).
10. [Narrow device overview](10-devices-overview-narrow.png).
11. [Narrow device details](11-device-details-narrow.png).

Remote hostname supplies the device name for a direct address with no chosen
alias/name. The address remains visible; SSH monitoring never grants execution.

To reproduce from `apps/desktop` with the existing Flutter cache:

```powershell
$env:ODESSY_FIX_CAPTURE_DIR = 'C:/path/to/new/capture-folder'
flutter test --no-pub test/ssh_direct_connect_test.dart test/ssh_connected_device_test.dart
```

Connections created by Connect are temporary; passwords are not saved.
Verify the real Jetson separately. No installer or installed app was changed.

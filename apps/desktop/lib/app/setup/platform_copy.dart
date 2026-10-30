import 'dart:io' show Platform;

/// Wording that depends on where setup registers the installed copy.
///
/// Windows creates a Start-menu shortcut plus an Installed-apps record; Linux
/// creates an application-menu entry plus an owned launcher. Keeping the strings
/// here stops the wizard from promising a Start menu on a platform without one.
String get registrationTargetCopy => Platform.isWindows
    ? 'and creates a Start-menu entry and an Installed-apps record.'
    : 'and creates an application-menu entry and a desktop launcher.';

String get accountScopeCopy => Platform.isWindows
    ? 'No administrator rights are requested; this is for your Windows account.'
    : 'No administrator rights are requested; this is for your user account.';

String get repairRegistrationLabel => Platform.isWindows
    ? 'Repair Start-menu entry'
    : 'Repair application-menu entry';

String get registrationRewrittenCopy => Platform.isWindows
    ? 'The Start-menu entry and Installed-apps record were rewritten.'
    : 'The application-menu entry was rewritten.';

String get serviceAccountCopy => Platform.isWindows
    ? 'The service runs for this Windows account.'
    : 'The service runs as a systemd user unit for this account.';

String get openFromMenuCopy => Platform.isWindows
    ? 'Open it now, or finish and open it from the Start menu. Setup closes after Odessy reports that it is ready.'
    : 'Open it now, or finish and open it from the application menu. Setup closes after Odessy reports that it is ready.';

String get registrationRowLabel =>
    Platform.isWindows ? 'Start menu' : 'Application menu';

String get registrationTileLabel => Platform.isWindows
    ? 'Start menu / Installed apps'
    : 'Application menu / installed apps';

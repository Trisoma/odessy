import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/setup/installed_startup.dart';
import 'package:odessy_desktop/app/setup/native_picker.dart';
import 'package:odessy_desktop/app/setup/setup_application.dart';
import 'package:odessy_desktop/main.dart';

/// The packaged build reaches these surfaces only through the entrypoint, so the
/// injected adapters are asserted here rather than assumed.
void main() {
  test('the entrypoint injects the real native picker and forwards the intent',
      () {
    final setup = applicationFor(const ['--setup', '--payload', r'C:\payload']);
    expect(setup, isA<SetupApplication>());
    expect((setup as SetupApplication).picker, isA<FileSelectorPicker>(),
        reason: 'without it the Browse buttons would be unavailable at runtime');
    expect(setup.payloadRoot, r'C:\payload');
  });

  test('an installed launch carries the id setup is waiting for', () {
    final installed = applicationFor(const [
      '--installed',
      '--install-root',
      r'C:\App',
      '--launch-id',
      'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    ]);
    expect(installed, isA<InstalledApplication>());
    expect((installed as InstalledApplication).launchId, 'b' * 32);
    expect(installed.installRoot, r'C:\App');
    expect(installed.picker, isA<FileSelectorPicker>());
  });

  test('an ordinary launch never opens an installer surface', () {
    expect(applicationFor(const []), isNot(isA<SetupApplication>()));
    expect(applicationFor(const []), isNot(isA<InstalledApplication>()));
  });
}

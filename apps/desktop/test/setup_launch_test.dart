import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/setup/setup_application.dart';
void main() {
  test('launch mode requires an explicit flag', () {
    expect(installationMode(['--setup']), 'setup');
    expect(installationMode(['--manage']), 'manage');
    expect(installationMode(['--installed', '--install-root', 'C:/App']),
        'installed');
    expect(installationMode([]), null);
  });
}

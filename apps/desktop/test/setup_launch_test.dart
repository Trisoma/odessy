import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/setup/setup_application.dart';
void main() {
  test('launch mode requires explicit setup flag', () {
    expect(installationMode(['--setup']), 'setup');
    expect(installationMode(['--manage']), 'manage');
    expect(installationMode([]), null);
  });
}

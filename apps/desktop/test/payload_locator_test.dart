import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/setup/payload_locator.dart';

void main() {
  late Directory root;

  setUp(() {
    root = Directory.systemTemp.createTempSync('odessy-payload-locator');
  });

  tearDown(() {
    if (root.existsSync()) root.deleteSync(recursive: true);
  });

  String executable() => '${root.path}${Platform.pathSeparator}OdessySetup';

  void plantPayload() {
    final payload = Directory('${root.path}${Platform.pathSeparator}payload')
      ..createSync();
    File('${payload.path}${Platform.pathSeparator}setup-context.json')
        .writeAsStringSync('{"schema_version":1}');
  }

  test('a bare launch of a packaged folder becomes a setup run', () {
    plantPayload();
    final resolved = resolveLaunchArguments(const [], executablePath: executable());
    expect(resolved, contains('--setup'));
    expect(resolved,
        containsAllInOrder(['--payload', '${root.path}${Platform.pathSeparator}payload']));
  });

  test('an installed copy without a sibling payload is left alone', () {
    final resolved = resolveLaunchArguments(const [], executablePath: executable());
    expect(resolved, isEmpty);
  });

  test('an explicit mode is preserved', () {
    plantPayload();
    final resolved = resolveLaunchArguments(const ['--manage'],
        executablePath: executable());
    expect(resolved, contains('--manage'));
    expect(resolved, isNot(contains('--setup')));
    expect(resolved, contains('--payload'));
  });

  test('an explicit payload path wins over the sibling one', () {
    plantPayload();
    final resolved = resolveLaunchArguments(
        const ['--payload', '/elsewhere'], executablePath: executable());
    expect(resolved.where((argument) => argument == '--payload').length, 1);
    expect(resolved, contains('/elsewhere'));
  });
}

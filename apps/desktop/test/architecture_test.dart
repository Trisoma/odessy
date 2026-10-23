import 'dart:io';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('presentation cannot introduce transport, providers or native execution',
      () {
    for (final file in Directory('lib/features')
        .listSync(recursive: true)
        .whereType<File>()
        .where((f) => f.path.endsWith('.dart'))) {
      final text = file.readAsStringSync();
      expect(
          text,
          isNot(matches(
              "(?:import|export)\\s+['\"]dart:(?:io|ffi|isolate)['\"]")),
          reason: file.path);
      expect(
          text,
          isNot(
              matches(r'\b(HttpClient|Process|Socket|File|Directory)\s*[.(]')),
          reason: file.path);
      expect(
          text,
          isNot(matches(
              r'\.api\.(send|approve|answer|settings|saveSettings|events)\(')),
          reason: file.path);
      expect(text, isNot(contains('package:agentic_')), reason: file.path);
      expect(
          text,
          isNot(matches(
              "(?:import|export)\\s+['\"]package:(?:http|dio|openai|anthropic|process_run)/")),
          reason: file.path);
    }
  });
  test('desktop does not install a second agent or provider runtime', () {
    final manifest = File('pubspec.yaml').readAsStringSync();
    expect(
        manifest,
        isNot(matches(RegExp(
            r'^\s+(agentic_\w*|dart_openai|anthropic_sdk|process_run):',
            multiLine: true))));
    final client =
        File('../../packages/odessy_client/pubspec.yaml').readAsStringSync();
    expect(client, isNot(matches(RegExp(r'^\s+flutter:', multiLine: true))));
  });
}

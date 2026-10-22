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
      expect(text, isNot(contains("'dart:io'")), reason: file.path);
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
    }
  });
}

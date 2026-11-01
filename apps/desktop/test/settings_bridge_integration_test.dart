import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/setup/local_setup_backend.dart';
import 'package:odessy_desktop/features/settings/controllers/component_settings_controller.dart';

void main() {
  final python = Platform.environment['ODESSY_TEST_PYTHON'];
  final home = Platform.environment['ODESSY_TEST_HOME'];
  for (final installed in [false, true]) {
    test(
        'real helper: ${installed ? 'installed' : 'source'} TTS-only save, reopen and conflict',
        () async {
      // Never run source configuration mutations against the developer's home.
      expect(Platform.environment[Platform.isWindows ? 'USERPROFILE' : 'HOME'],
          home);
      final repo = Directory.current.parent.parent.absolute.path;
      final temp =
          Directory.systemTemp.createTempSync('odessy-settings-integration-');
      var root = '';
      if (installed) {
        root = '${temp.path}/App';
        const fixture = r'''
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);version='a'*32;base=root/'versions'/version
(base/'Scripts').mkdir(parents=True);(base/'desktop').mkdir()
(base/'Scripts/python.exe').write_bytes(b'python')
(base/'desktop/odessy_desktop.exe').write_bytes(b'desktop')
selection={'role':'combined'}
(base/'selection.json').write_text(json.dumps({'schema_version':1,'selection':selection}))
files={p.relative_to(base).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob('*') if p.is_file()}
(root/'installation.json').write_text(json.dumps({'schema_version':1,'id':'b'*32,'root':str(root),'active':version,'versions':{version:files},'selection':selection}))
''';
        final created = await Process.run(python!, ['-c', fixture, root]);
        expect(created.exitCode, 0, reason: '${created.stderr}');
      }
      final model = File('${temp.path}/output.onnx')
        ..writeAsStringSync('fixture only, no inference');
      final backend = LocalSetupBackend(python: python!, sourceRoot: repo);
      final c = ComponentSettingsController(backend, root: root);
      ComponentSettingsController? reopened;
      try {
        await c.load();
        expect(c.error, isEmpty);
        final initial = jsonDecode(jsonEncode(c.snapshot));
        final patch = {
          'tts_provider': 'custom_audio:factory',
          'tts_model': model.path
        };
        expect(await c.save('tts', patch), isTrue, reason: c.error);
        reopened = ComponentSettingsController(backend, root: root);
        await reopened.load();
        expect(
            reopened.snapshot['selection']['voice']['tts_model'], model.path);
        expect(reopened.snapshot['selection']['model'],
            initial['selection']['model']);
        expect(reopened.snapshot['selection']['voice']['stt_provider'],
            initial['selection']['voice']['stt_provider']);
        expect(reopened.snapshot['selection']['voice']['enabled'],
            initial['selection']['voice']['enabled']);
        c.draft('tts', patch);
        c.edit('tts', {...patch, 'tts_model': ''});
        expect(await reopened.save('voice', {'enabled': true}), isTrue,
            reason: reopened.error);
        expect(await c.save('tts', c.drafts['tts']!.values), isFalse);
        expect(c.error, contains('changed'));
        expect(c.drafts['tts']!.dirty, isTrue);
        await reopened.load();
        expect(
            reopened.snapshot['selection']['voice']['tts_model'], model.path);
      } finally {
        await c.shutdown();
        c.dispose();
        if (reopened != null) {
          await reopened.shutdown();
          reopened.dispose();
        }
        await backend.close();
        temp.deleteSync(recursive: true);
      }
    },
        skip: python == null || home == null
            ? 'Requires explicit isolated-home helper qualification'
            : false,
        timeout: const Timeout(Duration(seconds: 90)));
  }
}

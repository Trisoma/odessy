import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/setup/installed_coordinator.dart';

void main() {
  test('session key reaches only the owned child environment, never argv or saved spec',()async{
    const key='fixture-private-session-key';
    final spec={'config':'/config.json','format':'selection','sha256':'a'*64,'selection':{'model':{'provider':'deepseek'}}};
    Map<String,String>? received;
    List<String>? argv;
    final child=InstalledCoordinator(python:'fixture',root:'',workspace:'',configuration:spec,sessionApiKey:key,
      portPicker:()async=>8765,launcher:(python,args,cwd,env)async{received=env;argv=args;throw const ProcessException('fixture',[]);});
    await expectLater(child.start(),throwsA(isA<CoordinatorStartFailure>()));
    expect(received!['DEEPSEEK_API_KEY'],key);
    expect(argv!.join(' '),isNot(contains(key)));
    expect(spec.toString(),isNot(contains(key)));
    expect(Platform.environment['DEEPSEEK_API_KEY'],isNot(key));
  });
  test('credential requirement honors explicit scope and never persists a key',(){
    final spec={'selection':{'model':{'provider':'deepseek','credential_env':'MY_KEY'}}};
    expect(InstalledCoordinator.credentialVariable(spec),'MY_KEY');
    expect(InstalledCoordinator.hasCredential(spec,{'DEEPSEEK_API_KEY':'different-scope'}),false);
    expect(InstalledCoordinator.hasCredential(spec,{'MY_KEY':'fixture'}),true);
    expect(InstalledCoordinator.credentialVariable({'selection':{'model':{'provider':'ollama'}}}),isNull);
  });
  test('a missing workspace never blocks the installed launch', () {
    expect(InstalledCoordinator.launchDirectory(''), isNull);
    expect(
        InstalledCoordinator.launchDirectory(
            '${Directory.systemTemp.path}${Platform.pathSeparator}absent-odessy-workspace'),
        isNull);
    expect(InstalledCoordinator.launchDirectory(Directory.systemTemp.path),
        Directory.systemTemp.path);
  });

  test('every launch uses an unguessable bearer token', () {
    final first = InstalledCoordinator.newToken();
    final second = InstalledCoordinator.newToken();
    expect(first.length, greaterThanOrEqualTo(32));
    expect(first, isNot(second));
    expect(first.contains('='), isFalse);
  });

  test('a start failure is reported without the token', () async {
    final coordinator = InstalledCoordinator(
        python: 'C:/missing/python.exe',
        root: 'C:/App',
        workspace: '',
        portPicker: () async => 8765,
        launcher: (python, arguments, cwd, environment) async =>
            throw const ProcessException('launch', []),
        probe: (base, token) async => false);
    await expectLater(
        coordinator.start(),
        throwsA(isA<CoordinatorStartFailure>().having(
            (failure) => failure.message, 'message',
            contains('could not be started'))));
  });
}

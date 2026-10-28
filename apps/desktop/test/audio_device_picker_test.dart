import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/app/setup/native_picker.dart';
import 'package:odessy_desktop/features/settings/presentation/provider_probe_panel.dart';

class Devices implements SetupBackend {
  final requests=<SetupRequest>[];
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    return SetupReply.fromJson({'id':request.id,'status':'ok','result':{'devices':[
      {'id':'pulse','name':'System audio server','input':true,'output':true}
    ]}},request.id);
  }
  @override
  Future<void> close() async {}
  @override
  Future<void> cancel() async {}
}

void main() {
  testWidgets('devices are detected without recording and selected without typing', (tester) async {
    final backend=Devices();
    final choices=<String>[];
    await tester.pumpWidget(MaterialApp(home:Scaffold(body:ProviderProbePanel(
      backend:backend,selection:const {'role':'combined','voice':{'audio_backend':'command'}},
      section:'voice',picker:const UnavailablePicker(),onDevice:(key,value)=>choices.add('$key=$value'),
    ))));
    await tester.pumpAndSettle();
    expect(backend.requests.length,1);
    expect(backend.requests.single.parameters['run'],isFalse);
    expect(find.text('System default'),findsNWidgets(2));
    await tester.tap(find.byKey(const ValueKey('audio_input_device-')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('System audio server').last);
    await tester.pumpAndSettle();
    expect(choices,['audio_input_device=pulse']);
    expect(backend.requests.length,1);
    await tester.pumpWidget(const SizedBox());
  });
}

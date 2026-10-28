import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';
class PairingFixture implements SetupBackend {
  final requests=<SetupRequest>[];
  @override Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    return SetupReply.fromJson({'id':request.id,'status':'ok','result':{
      'schema_version':1,'supported':false,'state':'closed','reason':'protocol_review_required'
    }},request.id);
  }
  @override Future<void> cancel() async {}
  @override Future<void> close() async {}
}
void main(){
  test('unreviewed protocol is explicit and codes travel only in bounded request payload',()async{
    final source=PairingFixture();final backend=SetupPairingBackend(source);
    final state=await backend.join('192.168.1.2:7444','012345');
    expect(state.supported,false);expect(state.reason,'protocol_review_required');
    expect(source.requests.single.parameters['code'],'012345');
    expect(source.requests.single.parameters['action'],'short-code');
    expect(source.requests.single.parameters.containsKey('argv'),false);
    await expectLater(backend.join('192.168.1.2:7444','12345'),throwsFormatException);
    expect(source.requests.length,1);
    await backend.close();
  });
  test('missing support and impossible success states are rejected',(){
    for(final value in [<String,dynamic>{}, {'schema_version':1,'supported':false,'state':'ready'}, {'schema_version':1,'supported':true,'state':'invented'}]){
      expect(()=>PairingSnapshot.fromJson(value),throwsFormatException);
    }
  });
}

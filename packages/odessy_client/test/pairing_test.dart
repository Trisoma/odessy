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
  test('session detail is additive, bounded and optional',(){
    final pending=PairingSnapshot.fromJson({'schema_version':1,'supported':true,'state':'awaiting_approval',
      'reason':'','session_id':'s','review_token':'t','seconds_remaining':90,'attempts_remaining':2,
      'peer':{'alias':'peer-jetson','installation_id':'jetson0001','fingerprint_sha256':'ab'*32}});
    expect(pending.awaitingApproval,true);
    expect(pending.paired,false);
    expect(pending.canStartOver,false);
    expect(pending.peerAlias,'peer-jetson');
    expect(pending.peerFingerprint.length,64);
    expect(pending.secondsRemaining,90);
    expect(pending.attemptsRemaining,2);
    final bare=PairingSnapshot.fromJson({'schema_version':1,'supported':false,'state':'closed'});
    expect(bare.peerAlias,'');
    expect(bare.attemptsRemaining,isNull);
    expect(bare.canStartOver,true);
    expect(bare.paired,false);
    // The outcome is unknown, so the surface must inspect rather than retry.
    expect(PairingSnapshot.fromJson({'schema_version':1,'supported':true,'state':'incomplete','session_id':'s'}).needsInspection,true);
    // A non-closed state without a session is never progress.
    expect(()=>PairingSnapshot.fromJson({'schema_version':1,'supported':true,'state':'ready'}),throwsFormatException);
  });
}

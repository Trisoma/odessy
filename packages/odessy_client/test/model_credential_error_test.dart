import 'dart:convert';
import 'dart:io';
import 'package:test/test.dart';
import 'package:odessy_client/odessy_client.dart';
void main(){
 test('missing credential response uses fixed copy and never returns server text',()async{
  final server=await HttpServer.bind(InternetAddress.loopbackIPv4,0);
  final listening=server.listen((request)async{
    await request.drain<void>();request.response.statusCode=503;
    request.response.write(jsonEncode({'detail':{'code':'model_credentials_missing','message':'private-untrusted-detail'}}));
    await request.response.close();
  });
  final api=LocalApi(Uri.parse('http://127.0.0.1:${server.port}'),'fixture');
  try{await expectLater(api.createSession(),throwsA(isA<ApiFailure>().having((e)=>e.code,'code','model_credentials_missing').having((e)=>e.toString(),'copy',allOf(contains('API key'),isNot(contains('private-untrusted-detail'))))));}
  finally{api.close();await listening.cancel();await server.close(force:true);}
 });
}
